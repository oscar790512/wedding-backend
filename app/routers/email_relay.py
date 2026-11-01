from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.services.email_relay import MAX_BODY_BYTES, authorize, check_receiver, receive_message

router = APIRouter(prefix="/internal/email-relay", tags=["internal-email-relay"])


async def authenticated_body(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "Relay request too large")
    raw = bytes(body)
    authorize(request.headers.get("X-Relay-Timestamp", ""), request.headers.get("X-Relay-Signature", ""), raw)
    return raw


@router.post("/send", include_in_schema=False)
async def send(request: Request):
    raw = await authenticated_body(request)
    return await run_in_threadpool(receive_message, raw)


@router.post("/check", include_in_schema=False)
async def check(request: Request):
    await authenticated_body(request)
    return await run_in_threadpool(check_receiver)
