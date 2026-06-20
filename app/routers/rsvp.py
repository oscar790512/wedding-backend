from fastapi import APIRouter, HTTPException, status

from app.database import get_supabase
from app.schemas.guest import GuestResponse, RsvpRequest

router = APIRouter(tags=["rsvp"])


@router.post("/rsvp", response_model=GuestResponse, status_code=status.HTTP_200_OK)
def submit_rsvp(payload: RsvpRequest) -> GuestResponse:
    if payload.status == "attend" and payload.total_adults < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Attending guests must include at least one adult",
        )

    supabase = get_supabase()
    guest_data = payload.model_dump()

    existing = (
        supabase.table("guests")
        .select("id")
        .eq("phone", payload.phone)
        .limit(1)
        .execute()
    )

    if existing.data:
        response = (
            supabase.table("guests")
            .update(guest_data)
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        response = supabase.table("guests").insert(guest_data).execute()

    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save RSVP",
        )

    return response.data[0]
