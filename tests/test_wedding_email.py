import io
import smtplib
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from app.auth import require_admin
from app.config import settings
from app.main import app
from app.routers import emails
from app.schemas.email import WeddingEmailContent
from app.services.wedding_email import build_message, preview_signature, qr_png, render_email, send_message

GUEST = {"id": "00000000-0000-4000-8000-000000000001", "name": "小明", "email": "guest@example.com", "checkin_token": "secret-checkin-token"}


class WeddingEmailTest(unittest.TestCase):
    def setUp(self):
        self.content = WeddingEmailContent()
        self.client = TestClient(app)
        app.dependency_overrides[require_admin] = lambda: {"username": "admin", "role": "admin"}

    def tearDown(self):
        app.dependency_overrides.clear()

    def payload(self):
        return {"guest_ids": [GUEST["id"]], "preview_guest_id": GUEST["id"], "content": self.content.model_dump()}

    def test_preview_has_all_sections_and_never_sends_or_writes(self):
        with patch.object(emails, "_load_guests", return_value=[GUEST]), patch.object(emails, "send_message") as smtp, patch.object(emails, "get_supabase") as db:
            response = self.client.post("/api/admin/email/preview", json=self.payload())
        self.assertEqual(response.status_code, 200)
        html = response.json()["html"]
        for value in ["小明", "2026 年 11 月 8 日", "12:30", "老新台菜", "停車", "提前", "@287rlhkg", "https://lin.ee/PvW0Voh", "/invitation", "data:image/png;base64,"]:
            self.assertIn(value, html)
        self.assertNotIn(GUEST["checkin_token"], html)
        smtp.assert_not_called()
        db.assert_not_called()

    def test_personalized_mime_uses_inline_png_and_plain_text(self):
        with patch.object(settings, "smtp_username", "sender@gmail.com"):
            message = build_message(GUEST, self.content, uuid4())
        self.assertEqual(str(message["To"]), GUEST["email"])
        self.assertIn("小明", message.get_body(preferencelist=("plain",)).get_content())
        self.assertIn("cid:checkin-qr", message.get_body(preferencelist=("html",)).get_content())
        images = [part for part in message.walk() if part.get_content_type() == "image/png"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["Content-ID"], "<checkin-qr>")
        self.assertEqual(images[0].get_filename(), "checkin-qr.png")
        self.assertEqual(images[0].get_payload(decode=True), qr_png(GUEST["checkin_token"]))
        self.assertGreaterEqual(Image.open(io.BytesIO(images[0].get_payload(decode=True))).width, 232)

    def test_html_escapes_names_and_text(self):
        html, _ = render_email({**GUEST, "name": '<img src=x onerror="alert(1)">'}, self.content.model_copy(update={"message": "<script>alert(1)</script>"}))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)

    def test_rejects_unsafe_urls_and_subject_newlines(self):
        for field, value in [("line_url", "javascript:alert(1)"), ("invitation_url", "https://good.com\nBcc:bad@example.com"), ("subject", "Hi\r\nBcc: bad@example.com")]:
            payload = self.payload()
            payload["content"][field] = value
            self.assertEqual(self.client.post("/api/admin/email/preview", json=payload).status_code, 422)

    def test_changed_guest_content_or_expired_preview_cannot_send(self):
        batch_id = uuid4()
        token = preview_signature(batch_id, int(time.time()) + 100, [GUEST], self.content)
        for change in ["guest", "content", "expired"]:
            payload = {**self.payload(), "batch_id": str(batch_id), "preview_token": token}
            guests = [GUEST]
            if change == "guest": guests = [{**GUEST, "checkin_token": "rotated"}]
            if change == "content": payload["content"]["message"] = "changed"
            if change == "expired": payload["preview_token"] = preview_signature(batch_id, int(time.time()) - 1, [GUEST], self.content)
            with patch.object(emails, "smtp_ready", return_value=True), patch.object(emails, "_load_guests", return_value=guests), patch.object(emails, "get_supabase") as db:
                response = self.client.post("/api/admin/email/send", json=payload)
            self.assertEqual(response.status_code, 409)
            db.assert_not_called()

    def test_smtp_configuration_required_before_enqueue(self):
        with patch.object(emails, "smtp_ready", return_value=False), patch.object(emails, "get_supabase") as db:
            response = self.client.post("/api/admin/email/send", json={**self.payload(), "batch_id": str(uuid4()), "preview_token": "token"})
            self.assertEqual(response.status_code, 503)
            db.assert_not_called()

    def test_confirm_enqueues_only_reviewed_snapshot(self):
        batch_id = uuid4()
        payload = {**self.payload(), "batch_id": str(batch_id), "preview_token": preview_signature(batch_id, int(time.time()) + 100, [GUEST], self.content)}
        db = MagicMock()
        with patch.object(emails, "smtp_ready", return_value=True), patch.object(emails, "_load_guests", return_value=[GUEST]), patch.object(emails, "get_supabase", return_value=db), patch.object(emails, "_run_batch") as run:
            response = self.client.post("/api/admin/email/send", json=payload)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(db.rpc.call_args.args[0], "enqueue_wedding_email")
        self.assertEqual(db.rpc.call_args.args[1]["p_guests"], [GUEST])
        self.assertEqual(db.rpc.call_args.args[1]["p_id"], str(batch_id))
        run.assert_called_once_with(str(batch_id))

    def test_email_endpoints_require_authentication(self):
        app.dependency_overrides.clear()
        self.assertIn(self.client.post("/api/admin/email/preview", json=self.payload()).status_code, (401, 403))

    def test_load_guests_excludes_deleted_declined_and_missing_tokens(self):
        for extra in [{"status": "decline"}, {"checkin_token": None}]:
            db = MagicMock()
            db.table.return_value.select.return_value.in_.return_value.is_.return_value.order.return_value.execute.return_value.data = [{**GUEST, "status": "attend", **extra}]
            with patch.object(emails, "get_supabase", return_value=db):
                with self.assertRaises(Exception) as error:
                    emails._load_guests([GUEST["id"]])
            self.assertEqual(error.exception.status_code, 409)
            db.table.return_value.select.return_value.in_.return_value.is_.assert_called_with("deleted_at", "null")

    def test_loading_many_guests_chunks_without_losing_later_recipients(self):
        guests = [{**GUEST, "id": str(uuid4()), "status": "attend"} for _ in range(205)]
        by_id = {row["id"]: row for row in guests}
        db = MagicMock()
        query = db.table.return_value.select.return_value
        query.in_.side_effect = lambda field, ids: SimpleNamespace(is_=lambda *_: SimpleNamespace(order=lambda *_: SimpleNamespace(execute=lambda: SimpleNamespace(data=[by_id[id] for id in ids]))))
        with patch.object(emails, "get_supabase", return_value=db):
            result = emails._load_guests([row["id"] for row in guests])
        self.assertEqual(len(result), 205)
        self.assertEqual(query.in_.call_count, 3)

    def test_smtp_uses_tls_and_one_recipient(self):
        with patch.object(settings, "smtp_port", 587), patch.object(settings, "smtp_username", "sender@gmail.com"), patch.object(settings, "smtp_password", "app password"), patch("app.services.wedding_email.smtplib.SMTP") as smtp:
            message = build_message(GUEST, self.content, uuid4())
            send_message(message)
        smtp.return_value.starttls.assert_called_once()
        smtp.return_value.login.assert_called_once_with("sender@gmail.com", "apppassword")
        smtp.return_value.send_message.assert_called_once_with(message, from_addr="sender@gmail.com", to_addrs=[GUEST["email"]])

    def test_disconnect_does_not_retry_and_marks_unknown(self):
        self._worker_failure(smtplib.SMTPServerDisconnected("lost"), "unknown")

    def test_auth_failure_pauses_without_marking_sent(self):
        self._worker_failure(smtplib.SMTPAuthenticationError(535, b"bad password"), "failed")

    def _worker_failure(self, exception, status):
        db = MagicMock()
        delivery = {**GUEST, "guest_id": GUEST["id"], "id": str(uuid4())}
        db.rpc.return_value.execute.return_value.data = {"state": "claimed", "delivery": delivery}
        with patch.object(emails, "get_supabase", return_value=db), patch.object(emails, "_batch", return_value={"sender": settings.smtp_username.lower(), "content": self.content.model_dump()}), patch.object(emails, "_load_guests", return_value=[GUEST]), patch.object(emails, "send_message", side_effect=exception) as send:
            emails._run_batch(str(uuid4()))
        send.assert_called_once()
        updates = [call.args[0] for call in db.table.return_value.update.call_args_list]
        self.assertIn(status, [row.get("status") for row in updates])
        self.assertNotIn("sent", [row.get("status") for row in updates])

    def test_quota_pauses_pending_queue_without_sending(self):
        db = MagicMock()
        db.rpc.return_value.execute.return_value.data = {"state": "quota"}
        with patch.object(emails, "get_supabase", return_value=db), patch.object(emails, "_batch", return_value={"sender": settings.smtp_username.lower(), "content": self.content.model_dump()}), patch.object(emails, "send_message") as send:
            emails._run_batch(str(uuid4()))
        send.assert_not_called()
        self.assertIn("24 小時", db.table.return_value.update.call_args.args[0]["last_error"])

    def test_ssl_and_socket_close_does_not_undo_accepted_message(self):
        with patch.object(settings, "smtp_port", 465), patch.object(settings, "smtp_username", "sender@gmail.com"), patch("app.services.wedding_email.smtplib.SMTP_SSL") as smtp:
            smtp.return_value.close.side_effect = OSError("socket closed")
            message = build_message(GUEST, self.content, uuid4())
            send_message(message)
        smtp.return_value.starttls.assert_not_called()
        smtp.return_value.send_message.assert_called_once()
        self.assertEqual(smtp.call_args.args[:2], (settings.smtp_host, 465))

    def test_connection_failure_is_safe_to_retry_because_no_message_was_submitted(self):
        from app.services.wedding_email import SmtpSetupError
        with patch.object(settings, "smtp_port", 465), patch("app.services.wedding_email.smtplib.SMTP_SSL", side_effect=OSError("connection refused")):
            with self.assertRaises(SmtpSetupError):
                send_message(build_message(GUEST, self.content, uuid4()))
        self._worker_failure(SmtpSetupError(), "failed")

    def test_staff_cannot_use_email_tools(self):
        from app.auth import get_current_admin
        app.dependency_overrides.pop(require_admin)
        app.dependency_overrides[get_current_admin] = lambda: {"username": "staff", "role": "staff"}
        self.assertEqual(self.client.get('/api/admin/email/overview').status_code, 403)

    def test_switching_preview_uses_that_recipients_own_name_and_qr(self):
        second = {**GUEST, 'id': '00000000-0000-4000-8000-000000000002', 'name': '小美', 'email': 'second@example.com', 'checkin_token': 'second-private-token'}
        payload = {**self.payload(), 'guest_ids': [GUEST['id'], second['id']], 'preview_guest_id': second['id']}
        with patch.object(emails, '_load_guests', return_value=[GUEST, second]):
            response = self.client.post('/api/admin/email/preview', json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['recipient'], {'name': '小美', 'email': 'second@example.com'})
        self.assertIn('小美 的報到碼', data['html'])
        self.assertNotIn('小明', data['html'])
        import base64
        self.assertIn(base64.b64encode(qr_png(second['checkin_token'])).decode(), data['html'])
        self.assertNotIn(base64.b64encode(qr_png(GUEST['checkin_token'])).decode(), data['html'])
