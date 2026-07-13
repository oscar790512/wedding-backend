from datetime import datetime, timezone
import secrets

from fastapi import APIRouter, HTTPException, status

from app.database import get_supabase
from app.schemas.guest import GuestResponse, RsvpRequest

router = APIRouter(tags=["rsvp"])


def _generate_checkin_token() -> str:
    return secrets.token_urlsafe(24)


def _create_unique_checkin_token() -> str:
    supabase = get_supabase()
    for _ in range(5):
        token = _generate_checkin_token()
        existing = (
            supabase.table("guests")
            .select("id")
            .eq("checkin_token", token)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return token
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to create check-in token",
    )


@router.post("/rsvp", response_model=GuestResponse, status_code=status.HTTP_200_OK)
def submit_rsvp(payload: RsvpRequest) -> GuestResponse:
    if payload.status == "attend" and payload.total_adults < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Attending guests must include at least one adult",
        )

    supabase = get_supabase()
    guest_data = payload.model_dump()
    guest_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    existing = (
        supabase.table("guests")
        .select("id,checkin_token")
        .eq("phone", payload.phone)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
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
