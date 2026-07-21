from datetime import datetime, timezone
import secrets

from fastapi import APIRouter, HTTPException, Request, status

from app.database import execute_read, get_supabase
from app.rate_limit import enforce_rsvp_rate_limit
from app.schemas.guest import GuestResponse, RsvpRequest
from app.schemas.settings import RsvpSettingsResponse

router = APIRouter(tags=["rsvp"])


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
