import base64
import logging
import smtplib
import time
import httpx
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from postgrest.exceptions import APIError

from app.auth import require_admin
from app.config import settings
from app.database import execute_idempotent_write, execute_read, get_supabase
from app.schemas.email import EmailPreviewRequest, EmailResumeRequest, EmailSendRequest, WeddingEmailContent
from app.services.wedding_email import (
    CAMPAIGN, SmtpSetupError, build_message, preview_signature, qr_png, render_email,
    send_message, smtp_ready, verify_preview,
)

router = APIRouter(prefix="/admin/email", tags=["email"])
logger = logging.getLogger(__name__)
PUBLIC_COLUMNS = "id,batch_id,guest_id,name,email,status,error,attempted_at,sent_at,created_at"


def _all_rows(query):
    rows = []
    for start in range(0, 100000, 500):
        page = execute_read(query().range(start, start + 499)).data
        rows.extend(page)
        if len(page) < 500:
            return rows
    raise HTTPException(422, "資料量過大，請縮小寄送範圍")


def _load_guests(guest_ids):
    ids = sorted({str(value) for value in guest_ids})
    result = []
    # Bound URL lengths and do not rely on PostgREST's default row limit.
    for start in range(0, len(ids), 100):
        result.extend(execute_read(get_supabase().table("guests").select("id,name,email,status,checkin_token")
            .in_("id", ids[start:start + 100]).is_("deleted_at", "null").order("id")).data)
    by_id = {row["id"]: row for row in result}
    guests = []
    for guest_id in ids:
        row = by_id.get(guest_id)
        if not row or row["status"] != "attend" or not row.get("checkin_token"):
            raise HTTPException(409, "名單已異動，請重新選取有報到碼的出席賓客並預覽")
        email = str(row.get("email") or "").strip()
        if not email or "@" not in email or any(c.isspace() or ord(c) < 32 for c in email) or any(c in email for c in '<>,;'):
            raise HTTPException(422, "選取的賓客有無效 Email，請先修正賓客資料")
        guests.append({"id": guest_id, "name": row["name"], "email": email, "checkin_token": row["checkin_token"]})
    return guests


def _batch(batch_id):
    rows = execute_read(get_supabase().table("wedding_email_batches").select("*").eq("id", str(batch_id)).limit(1)).data
    if not rows:
        raise HTTPException(404, "找不到寄送批次")
    return rows[0]


def _require_smtp():
    if not smtp_ready():
        raise HTTPException(503, "尚未設定 Gmail SMTP。請在後端設定 SMTP_USERNAME 與 SMTP_PASSWORD（應用程式密碼）。")


def _run_batch(batch_id):
    db = get_supabase()
    phase = "load_batch"
    try:
        batch = _batch(batch_id)
        if batch["sender"] != settings.smtp_username.lower():
            raise ValueError("寄件帳號已變更，請確認後使用原帳號繼續寄送")
        content = WeddingEmailContent(**batch["content"])
        execute_idempotent_write(db.table("wedding_email_batches").update({"last_error": None}).eq("id", batch_id))
        while True:
            phase = "claim"
            claim_request = db.rpc("claim_wedding_email_v2", {
                "p_batch": batch_id, "p_sender": batch["sender"],
                "p_limit": max(1, settings.smtp_daily_limit),
                "p_interval": max(1.0, settings.smtp_interval_seconds),
                "p_request_id": str(uuid4()),
            })
            # Only retry this RPC because the DB replays the same reservation by ID.
            result = execute_idempotent_write(claim_request).data
            if result["state"] == "wait":
                time.sleep(min(max(result["seconds"], 0.1), 10))
                continue
            if result["state"] == "quota":
                execute_idempotent_write(db.table("wedding_email_batches").update({"last_error": "已達系統滾動 24 小時寄送上限，待額度恢復後按繼續寄送。"}).eq("id", batch_id))
                return
            if result["state"] == "empty":
                return
            delivery = result["delivery"]
            guest = {"id": delivery["guest_id"], "name": delivery["name"], "email": delivery["email"], "checkin_token": delivery["checkin_token"]}
            outcome = {"status": "sent", "error": None}
            pause = False
            phase = "prepare_message"
            try:
                current = _load_guests([delivery["guest_id"]])[0]
                if current != guest:
                    raise ValueError("賓客姓名、Email 或報到碼已變更，請確認資料後再寄送")
                message = build_message(guest, content, batch_id)
            except httpx.TransportError:
                outcome = {"status": "failed", "error": "暫時無法讀取賓客資料，此封尚未送出，請稍後重試。"}
                pause = True
            except Exception:
                outcome = {"status": "failed", "error": "賓客資料已異動或信件無法建立，請確認賓客資料後再試。"}
            else:
                try:
                    phase = "smtp"
                    send_message(message)
                    outcome["sent_at"] = datetime.now(timezone.utc).isoformat()
                except smtplib.SMTPAuthenticationError:
                    outcome = {"status": "failed", "error": "SMTP 驗證失敗，請檢查應用程式密碼。"}
                    pause = True
                except SmtpSetupError:
                    outcome = {"status": "failed", "error": "SMTP 連線失敗，請檢查主機、連接埠與網路後重試。"}
                    pause = True
                except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
                    outcome = {"status": "failed", "error": "SMTP 拒絕此封信件，請確認地址、帳號額度及 Gmail 狀態後重試。"}
                    pause = True
                except Exception:
                    # A disconnect after DATA may mean Gmail accepted the message.
                    outcome = {"status": "unknown", "error": "無法確認 Gmail 是否已接受，請先查看寄件備份，避免重寄。"}
                    pause = True
            phase = "persist_result"
            result_update = db.table("wedding_email_deliveries").update(outcome).eq("id", delivery["id"])
            if delivery.get("attempted_at"):
                result_update = result_update.eq("attempted_at", delivery["attempted_at"])
            # Retrying this guarded assignment cannot submit another SMTP message.
            execute_idempotent_write(result_update)
            if pause:
                execute_idempotent_write(db.table("wedding_email_batches").update({"last_error": outcome["error"]}).eq("id", batch_id))
                return
    except Exception as exc:
        logger.error("Wedding email batch stopped. Batch: %s Phase: %s ErrorType: %s", batch_id, phase, type(exc).__name__)
        try:
            message = "寄送已暫停，請檢查寄送狀態後繼續；狀態待確認的信件不會自動重寄。"
            if phase == "persist_result":
                message = "寄送結果暫時無法儲存，請先確認 Gmail 寄件備份，避免重複寄送；系統會持續更新狀態。"
            elif phase == "claim":
                message = "領取寄送任務時資料庫連線中斷，請先更新狀態；已領取但未確認的項目不會自動重寄。"
            execute_idempotent_write(db.table("wedding_email_batches").update({"last_error": message}).eq("id", batch_id))
        except Exception:
            pass


@router.get("/overview")
def overview(_admin=Depends(require_admin)):
    content = WeddingEmailContent(invitation_url=settings.wedding_invitation_url)
    response = {"smtp_ready": smtp_ready(), "sender": settings.smtp_username, "daily_limit": settings.smtp_daily_limit,
                "content": content.model_dump(), "storage_ready": True, "deliveries": [], "batches": []}
    try:
        response["deliveries"] = _all_rows(lambda: get_supabase().table("wedding_email_deliveries").select(PUBLIC_COLUMNS).eq("campaign", CAMPAIGN).order("id"))
        response["batches"] = execute_read(get_supabase().table("wedding_email_batches").select("id,created_at,last_error,sender").eq("campaign", CAMPAIGN).order("created_at", desc=True).limit(20)).data
    except APIError as exc:
        if exc.code not in ("PGRST205", "42P01"):
            raise
        response["storage_ready"] = False
    return response


@router.post("/preview")
def preview(payload: EmailPreviewRequest, _admin=Depends(require_admin)):
    guests = _load_guests(payload.guest_ids)
    guest = next((row for row in guests if row["id"] == str(payload.preview_guest_id)), None)
    if not guest:
        raise HTTPException(422, "預覽對象必須在收件名單內")
    batch_id = uuid4()
    expires = int(time.time()) + 1800
    image_src = "data:image/png;base64," + base64.b64encode(qr_png(guest["checkin_token"])).decode()
    html, text = render_email(guest, payload.content, image_src)
    return {"batch_id": str(batch_id), "preview_token": preview_signature(batch_id, expires, guests, payload.content),
            "html": html, "text": text, "subject": payload.content.subject, "recipient": {"name": guest["name"], "email": guest["email"]},
            "total": len(guests), "expires_at": expires}


@router.post("/send", status_code=202)
def send(payload: EmailSendRequest, background: BackgroundTasks, admin=Depends(require_admin)):
    _require_smtp()
    guests = _load_guests(payload.guest_ids)
    if not verify_preview(payload.preview_token, payload.batch_id, guests, payload.content):
        raise HTTPException(409, "預覽已過期，或名單／信件內容已變更，請重新預覽後再寄送")
    get_supabase().rpc("enqueue_wedding_email", {"p_id": str(payload.batch_id), "p_campaign": CAMPAIGN,
        "p_sender": settings.smtp_username.lower(), "p_actor": admin["username"],
        "p_content": payload.content.model_dump(), "p_guests": guests}).execute()
    background.add_task(_run_batch, str(payload.batch_id))
    return {"batch_id": str(payload.batch_id)}


@router.get("/batches/{batch_id}")
def batch_status(batch_id: UUID, _admin=Depends(require_admin)):
    batch = _batch(batch_id)
    stale_before = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    deliveries = _all_rows(lambda: get_supabase().table("wedding_email_deliveries").select(PUBLIC_COLUMNS).eq("batch_id", str(batch_id)).order("id"))
    stale_rows = [row for row in deliveries if row["status"] == "sending" and row.get("attempted_at")
                  and datetime.fromisoformat(row["attempted_at"].replace("Z", "+00:00")) < datetime.fromisoformat(stale_before)]
    if stale_rows:
        outcome = {"status": "unknown", "error": "寄送中斷，請先至 Gmail 寄件備份確認，勿直接重寄。"}
        updated = execute_idempotent_write(get_supabase().table("wedding_email_deliveries").update(outcome)
            .eq("batch_id", str(batch_id)).eq("status", "sending").lt("attempted_at", stale_before)).data
        changed_ids = {row["id"] for row in updated}
        for row in deliveries:
            if row["id"] in changed_ids:
                row.update(outcome)
    return {"batch_id": str(batch_id), "last_error": batch["last_error"], "deliveries": deliveries}


@router.post("/batches/{batch_id}/resume", status_code=202)
def resume(batch_id: UUID, payload: EmailResumeRequest, background: BackgroundTasks, _admin=Depends(require_admin)):
    _require_smtp()
    batch = _batch(batch_id)
    if batch["sender"] != settings.smtp_username.lower():
        raise HTTPException(409, "請使用此批次原本的寄件帳號繼續寄送")
    if payload.retry_failed:
        get_supabase().table("wedding_email_deliveries").update({"status": "pending", "error": None}).eq("batch_id", str(batch_id)).eq("status", "failed").execute()
    background.add_task(_run_batch, str(batch_id))
    return {"batch_id": str(batch_id)}
