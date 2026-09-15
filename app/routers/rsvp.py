from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import urllib.error
import urllib.request

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.config import settings
from app.database import execute_read, get_supabase
from app.rate_limit import enforce_rsvp_rate_limit
from app.schemas.guest import (
    GuestResponse,
    RsvpRequest,
    SeatLookupRequest,
    SeatVideoLookupResponse,
)
from app.schemas.settings import RsvpSettingsResponse

router = APIRouter(tags=["rsvp"])
LINE_REPLY_ENDPOINT = "https://api.line.me/v2/bot/message/reply"
LINE_LOOKUP_SESSION_TTL = timedelta(minutes=10)
SEAT_VIDEO_DIR = Path(__file__).resolve().parent.parent / "static" / "seat-videos"
line_lookup_sessions: dict[str, datetime] = {}


@router.get("/rsvp/settings", response_model=RsvpSettingsResponse)
def get_rsvp_settings() -> RsvpSettingsResponse:
    response = execute_read(
        get_supabase()
        .table("wedding_settings")
        .select("rsvp_deadline,updated_at")
        .eq("id", 1)
        .limit(1)
    )
    if not response.data:
        return RsvpSettingsResponse()
    return RsvpSettingsResponse.model_validate(response.data[0])


def _public_url(request: Request, path: str) -> str:
    base_url = settings.public_base_url.strip().rstrip("/")
    if not base_url:
        base_url = str(request.base_url).rstrip("/")
    return f"{base_url}{path}"


def _table_video_filename(table_name: str | None) -> str | None:
    if not table_name:
        return None

    manifest_path = SEAT_VIDEO_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest:
            if item.get("table_name") == table_name:
                return item.get("video_filename")

    if table_name == "主桌":
        return "main-table.mp4"

    match = re.search(r"\d+", table_name)
    if match:
        return f"table-{int(match.group()):02d}.mp4"

    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", table_name).strip("-").lower()
    return f"{safe_name}.mp4" if safe_name else None


def _seat_message_text(guest: dict, phone_last5: str, attendee_count: int) -> str:
    table_name = guest.get("allocated_table")
    if not table_name:
        table_name = "座位安排中，請洽現場工作人員"
    return (
        f"{guest['name']}，電話後五碼 {phone_last5}\n"
        f"桌次：{table_name}\n"
        f"出席總人數：{attendee_count} 位"
    )


def _verify_line_signature(body: bytes, signature: str | None) -> bool:
    if not settings.line_channel_secret or not signature:
        return False
    digest = hmac.new(
        settings.line_channel_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def _reply_line_message(reply_token: str, messages: list[dict]) -> None:
    if not settings.line_channel_access_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LINE channel access token is not configured",
        )

    body = json.dumps(
        {
            "replyToken": reply_token,
            "messages": messages,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        LINE_REPLY_ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {settings.line_channel_access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        urllib.request.urlopen(request, timeout=8).close()
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to reply to LINE",
        ) from exc


def _line_text_message(text: str) -> dict:
    return {
        "type": "text",
        "text": text,
    }


def _line_event_source_key(event: dict) -> str | None:
    source = event.get("source") or {}
    for key in ("userId", "groupId", "roomId"):
        if source.get(key):
            return f"{source.get('type', 'unknown')}:{source[key]}"
    return None


def _lookup_keyword() -> str:
    return settings.line_seat_lookup_keyword.strip() or "我坐哪啊？"


def _extract_phone_last5(text: str) -> str | None:
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits if len(digits) == 5 else None


def _start_line_lookup_session(source_key: str | None) -> None:
    if source_key:
        line_lookup_sessions[source_key] = datetime.now(timezone.utc) + LINE_LOOKUP_SESSION_TTL


def _has_active_line_lookup_session(source_key: str | None) -> bool:
    if not source_key:
        return False
    expires_at = line_lookup_sessions.get(source_key)
    if not expires_at:
        return False
    if expires_at < datetime.now(timezone.utc):
        line_lookup_sessions.pop(source_key, None)
        return False
    return True


def _clear_line_lookup_session(source_key: str | None) -> None:
    if source_key:
        line_lookup_sessions.pop(source_key, None)


@router.post("/seat-video-lookup", response_model=SeatVideoLookupResponse)
def lookup_seat_video(
    payload: SeatLookupRequest,
    request: Request,
) -> SeatVideoLookupResponse:
    supabase = get_supabase()
    guest_response = execute_read(
        supabase.table("guests")
        .select("name,phone,status,total_adults,total_children,allocated_table")
        .eq("status", "attend")
        .like("phone", f"%{payload.phone_last5}")
        .is_("deleted_at", "null")
    )
    matched_guests = guest_response.data or []

    if not matched_guests:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到出席資料，請確認電話後五碼或洽現場工作人員",
        )

    if len(matched_guests) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="查到多筆資料，請洽現場工作人員協助確認座位",
        )

    guest = matched_guests[0]
    total_adults = int(guest.get("total_adults") or 0)
    total_children = int(guest.get("total_children") or 0)
    attendee_count = total_adults + total_children
    video_filename = _table_video_filename(guest.get("allocated_table"))
    video_url = None
    preview_image_url = None
    line_video_message = None

    if video_filename:
        poster_filename = video_filename.removesuffix(".mp4") + ".png"
        video_url = _public_url(request, f"/static/seat-videos/{video_filename}")
        preview_image_url = _public_url(request, f"/static/seat-videos/{poster_filename}")
        line_video_message = {
            "type": "video",
            "originalContentUrl": video_url,
            "previewImageUrl": preview_image_url,
        }

    return {
        "guest": {
            "name": guest["name"],
            "total_adults": total_adults,
            "total_children": total_children,
            "attendee_count": attendee_count,
            "allocated_table": guest.get("allocated_table"),
            "phone_last5": payload.phone_last5,
        },
        "message_text": _seat_message_text(guest, payload.phone_last5, attendee_count),
        "video_filename": video_filename,
        "video_url": video_url,
        "preview_image_url": preview_image_url,
        "line_video_message": line_video_message,
    }


@router.post("/line/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: str | None = Header(default=None),
) -> dict[str, str]:
    body = await request.body()
    if not _verify_line_signature(body, x_line_signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid LINE signature",
        )

    payload = json.loads(body.decode("utf-8"))
    for event in payload.get("events", []):
        reply_token = event.get("replyToken")
        message = event.get("message") or {}
        if not reply_token or message.get("type") != "text":
            continue

        text = message.get("text", "").strip()
        source_key = _line_event_source_key(event)
        keyword = _lookup_keyword()
        has_keyword = keyword in text
        phone_last5 = _extract_phone_last5(text)

        if has_keyword and not phone_last5:
            _start_line_lookup_session(source_key)
            _reply_line_message(
                reply_token,
                [_line_text_message("請輸入電話後五碼，例如：45678")],
            )
            continue

        if not has_keyword and not _has_active_line_lookup_session(source_key):
            if phone_last5:
                _reply_line_message(
                    reply_token,
                    [_line_text_message(f"請先輸入「{keyword}」，再輸入電話後五碼。")],
                )
            continue

        if not phone_last5:
            _reply_line_message(
                reply_token,
                [_line_text_message(f"請輸入「{keyword}」後，再輸入電話後五碼，例如：45678")],
            )
            continue

        try:
            result = lookup_seat_video(
                SeatLookupRequest(phone_last5=phone_last5),
                request,
            )
        except HTTPException as exc:
            _reply_line_message(reply_token, [_line_text_message(str(exc.detail))])
            continue

        messages = [_line_text_message(result["message_text"])]
        if result.get("line_video_message"):
            messages.append(result["line_video_message"])
        _reply_line_message(reply_token, messages)
        _clear_line_lookup_session(source_key)

    return {"status": "ok"}


def _generate_checkin_token() -> str:
    return secrets.token_urlsafe(24)


def _create_unique_checkin_token() -> str:
    return _generate_checkin_token()


@router.post("/rsvp", response_model=GuestResponse, status_code=status.HTTP_200_OK)
def submit_rsvp(request: Request, payload: RsvpRequest) -> GuestResponse:
    enforce_rsvp_rate_limit(request, payload.phone)

    if payload.status == "attend" and payload.total_adults < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Attending guests must include at least one adult",
        )

    supabase = get_supabase()
    guest_data = payload.model_dump()
    guest_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    existing = execute_read(
        supabase.table("guests")
        .select("id,checkin_token")
        .eq("phone", payload.phone)
        .is_("deleted_at", "null")
        .limit(1)
    )

    if existing.data:
        current = existing.data[0]
        if payload.status == "attend" and not current.get("checkin_token"):
            guest_data["checkin_token"] = _create_unique_checkin_token()
            guest_data["checkin_token_rotated_at"] = guest_data["updated_at"]
        response = (
            supabase.table("guests")
            .update(guest_data)
            .eq("id", current["id"])
            .execute()
        )
    else:
        if payload.status == "attend":
            guest_data["checkin_token"] = _create_unique_checkin_token()
            guest_data["checkin_token_rotated_at"] = guest_data["updated_at"]
        response = supabase.table("guests").insert(guest_data).execute()

    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save RSVP",
        )

    return response.data[0]
