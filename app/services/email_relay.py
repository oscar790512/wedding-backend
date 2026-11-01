"""Authenticated HTTPS transport; the receiver owns durable, at-most-once SMTP submission."""
import base64
import hashlib
import hmac
import json
import logging
import smtplib
import time
from contextlib import suppress
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from fastapi import HTTPException

from app.config import settings
from app.database import execute_idempotent_write, get_supabase
from app.services.wedding_email import RelayNotSubmittedError, SmtpSetupError, connect_smtp, send_smtp_message

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 1_000_000
MAX_CLOCK_SKEW = 300


class RelayOutcomeUnknown(Exception):
    """The relay may have submitted the message. Never automatically resubmit."""


def relay_client_ready():
    try:
        url = urlsplit(settings.email_relay_url)
    except ValueError:
        return False
    return bool(url.scheme == "https" and url.hostname and not url.username and not url.password
                and not url.query and not url.fragment and len(settings.email_relay_secret) >= 32
                and settings.smtp_username)


def signature(timestamp, body):
    return hmac.new(settings.email_relay_secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def authorize(timestamp, signed, body):
    if not settings.email_relay_enabled or len(settings.email_relay_secret) < 32:
        raise HTTPException(404, "Not found")
    try:
        valid_time = abs(time.time() - int(timestamp)) <= MAX_CLOCK_SKEW
    except (ValueError, TypeError):
        valid_time = False
    if not valid_time or not hmac.compare_digest(signed.encode(), signature(timestamp, body).encode()):
        raise HTTPException(401, "Invalid relay signature")


def send_via_relay(message):
    if not relay_client_ready():
        raise SmtpSetupError("Email relay is not configured")
    # Message-ID identifies this particular send attempt, not the guest or email address.
    if not message.get("Message-ID"):
        raise SmtpSetupError("Message-ID is required")
    body = json.dumps({"request_id": str(uuid5(NAMESPACE_URL, str(message["Message-ID"]))),
                       "message": base64.b64encode(message.as_bytes(policy=policy.SMTP)).decode()},
                      separators=(",", ":")).encode()
    if len(body) > MAX_BODY_BYTES:
        raise SmtpSetupError("Email exceeds relay size limit")
    timestamp = str(int(time.time()))
    try:
        # No redirects, automatic retries or direct-SMTP fallback: response loss is ambiguous.
        with httpx.Client(timeout=120, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as client:
            response = client.post(settings.email_relay_url, content=body, headers={
                "Content-Type": "application/json", "X-Relay-Timestamp": timestamp,
                "X-Relay-Signature": signature(timestamp, body)})
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RelayOutcomeUnknown from exc
    if not isinstance(result, dict) or result.get("request_id") != json.loads(body)["request_id"]:
        raise RelayOutcomeUnknown
    if result.get("status") == "sent":
        return
    if result.get("status") == "failed":
        if result.get("reason") == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"Relay SMTP authentication failed")
        raise RelayNotSubmittedError(result.get("reason"))
    raise RelayOutcomeUnknown


def parse_message(body):
    try:
        data = json.loads(body)
        request_id = str(UUID(data["request_id"]))
        message = BytesParser(policy=policy.SMTP).parsebytes(base64.b64decode(data["message"], validate=True))
        addresses = getaddresses(message.get_all("To", []))
        senders = getaddresses(message.get_all("From", []))
        if (len(addresses) != 1 or len(senders) != 1 or not addresses[0][1]
                or "@" not in addresses[0][1] or any(c.isspace() for c in addresses[0][1])
                or senders[0][1].lower() != settings.smtp_username.lower()
                or message.get_all("Cc") or message.get_all("Bcc")
                or len(message.get_all("Message-ID", [])) != 1):
            raise ValueError("Invalid envelope")
        return request_id, message
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(422, "Invalid relay message") from exc


def receive_message(body):
    request_id, message = parse_message(body)
    db = get_supabase()
    # Intentionally do NOT retry reservation: a committed-but-lost result must not grant a second owner.
    try:
        claim = db.rpc("reserve_wedding_email_relay", {
            "p_id": request_id, "p_sender": settings.smtp_username.lower(),
            "p_hash": hashlib.sha256(body).hexdigest(),
            "p_limit": max(1, settings.smtp_daily_limit),
            "p_interval": max(1.0, settings.smtp_interval_seconds),
        }).execute().data
    except Exception as exc:
        logger.error("Email relay reservation unavailable: %s", type(exc).__name__)
        return {"request_id": request_id, "status": "unknown"}
    state = claim["state"]
    if state != "claimed":
        status = "failed" if state in ("quota", "wait") else claim.get("status", "unknown")
        return {"request_id": request_id, "status": status, "reason": claim.get("reason", state)}
    result = {"status": "sent", "reason": None}
    try:
        # Check current setup only after replay lookup: a config change cannot erase an accepted/uncertain outcome.
        if not settings.smtp_username or not settings.smtp_password or settings.smtp_port not in (465, 587):
            raise SmtpSetupError
        # Always direct SMTP here, even if an outbound relay URL is accidentally configured.
        send_smtp_message(message)
    except smtplib.SMTPAuthenticationError:
        result = {"status": "failed", "reason": "auth"}
    except SmtpSetupError:
        result = {"status": "failed", "reason": "setup"}
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        result = {"status": "failed", "reason": "rejected"}
    except Exception:
        result = {"status": "unknown", "reason": "smtp"}
    try:
        execute_idempotent_write(db.table("wedding_email_relay_requests").update(result)
                                 .eq("id", request_id).eq("status", "sending"))
    except Exception as exc:
        logger.error("Email relay outcome unavailable: %s", type(exc).__name__)
        return {"request_id": request_id, "status": "unknown"}
    return {"request_id": request_id, **result}


def check_receiver():
    """Authenticated operational probe; never calls SMTP DATA or writes a receipt."""
    try:
        get_supabase().table("wedding_email_relay_requests").select("id").limit(1).execute()
    except Exception:
        return {"status": "unavailable", "reason": "storage"}
    if not settings.smtp_username or not settings.smtp_password or settings.smtp_port not in (465, 587):
        return {"status": "unavailable", "reason": "setup"}
    try:
        client = connect_smtp()
    except smtplib.SMTPAuthenticationError:
        return {"status": "unavailable", "reason": "auth"}
    except Exception:
        return {"status": "unavailable", "reason": "connection"}
    with suppress(OSError):
        client.close()
    return {"status": "ready", "sender": settings.smtp_username.lower()}
