import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import get_current_admin
from app.database import get_supabase
from app.schemas.guest import AdminSummary, GuestCheckinUpdate, GuestResponse

router = APIRouter(prefix="/admin", tags=["admin"])

_SEARCH_UNSAFE = re.compile(r"[,().]")


def _sanitize_search(value: str) -> str:
    return _SEARCH_UNSAFE.sub("", value.strip())


@router.get("/summary", response_model=AdminSummary)
def get_summary(_admin: dict = Depends(get_current_admin)) -> AdminSummary:
    response = get_supabase().table("guests").select("*").execute()
    guests = response.data or []

    attending = [g for g in guests if g["status"] == "attend"]
    total_adults = sum(g["total_adults"] for g in attending)
    total_children = sum(g["total_children"] for g in attending)

    return AdminSummary(
        total_guests=len(guests),
        attending_households=len(attending),
        total_adults=total_adults,
        total_children=total_children,
        total_attendees=total_adults + total_children,
        vegetarian_count=sum(
            1
            for g in attending
            if g.get("diet_notes") and g["diet_notes"].strip()
        ),
        cake_count=sum(1 for g in attending if g.get("need_cake")),
        total_gift_amount=sum(
            (Decimal(str(g.get("gift_amount") or 0)) for g in guests),
            Decimal("0"),
        ),
        arrived_count=sum(1 for g in guests if g.get("is_arrived")),
    )


@router.get("/guests", response_model=list[GuestResponse])
def list_guests(
    q: str | None = Query(default=None, max_length=100),
    _admin: dict = Depends(get_current_admin),
) -> list[GuestResponse]:
    query = get_supabase().table("guests").select("*").order("created_at")

    if q:
        search = _sanitize_search(q)
        if search:
            pattern = f"%{search}%"
            query = query.or_(f"name.ilike.{pattern},phone.ilike.{pattern}")

    response = query.execute()
    return response.data or []


@router.patch("/guests/{guest_id}", response_model=GuestResponse)
def update_guest_checkin(
    guest_id: str,
    payload: GuestCheckinUpdate,
    _admin: dict = Depends(get_current_admin),
) -> GuestResponse:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    response = (
        get_supabase()
        .table("guests")
        .update(updates)
        .eq("id", guest_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Guest not found",
        )

    return response.data[0]
