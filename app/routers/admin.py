import re
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import get_current_admin
from app.database import get_supabase
from app.schemas.guest import (
    AdminGuestCreate,
    AdminGuestUpdate,
    AdminSummary,
    GuestResponse,
    GuestStatus,
    ShippingFilter,
    TableSettingResponse,
    TableSettingUpsert,
)

router = APIRouter(prefix="/admin", tags=["admin"])

_SEARCH_UNSAFE = re.compile(r"[,().]")


def _sanitize_search(value: str) -> str:
    return _SEARCH_UNSAFE.sub("", value.strip())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_guests_query():
    return get_supabase().table("guests").select("*").is_("deleted_at", "null")


def _load_guest_or_404(guest_id: str) -> dict:
    response = (
        get_supabase()
        .table("guests")
        .select("*")
        .eq("id", guest_id)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Guest not found",
        )
    return response.data[0]


@router.get("/table-settings", response_model=list[TableSettingResponse])
def list_table_settings(
    _admin: dict = Depends(get_current_admin),
) -> list[TableSettingResponse]:
    response = (
        get_supabase()
        .table("table_settings")
        .select("table_name,capacity,updated_at")
        .order("table_name")
        .execute()
    )
    return response.data or []


@router.post("/table-settings", response_model=TableSettingResponse)
def upsert_table_setting(
    payload: TableSettingUpsert,
    _admin: dict = Depends(get_current_admin),
) -> TableSettingResponse:
    data = payload.model_dump(mode="json")
    data["updated_at"] = _utc_now()

    response = (
        get_supabase()
        .table("table_settings")
        .upsert(data, on_conflict="table_name")
        .execute()
    )
    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save table setting",
        )
    return response.data[0]


@router.get("/summary", response_model=AdminSummary)
def get_summary(_admin: dict = Depends(get_current_admin)) -> AdminSummary:
    response = _active_guests_query().execute()
    guests = response.data or []

    attending = [g for g in guests if g["status"] == "attend"]
    declined = [g for g in guests if g["status"] == "decline"]
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
        invitation_count=sum(
            1 for g in attending if g.get("need_invitation")
        ),
        child_seats_count=sum(g.get("child_seats") or 0 for g in attending),
        decline_blessing_only_count=sum(
            1 for g in declined if g.get("decline_response") == "blessing_only"
        ),
        decline_request_cake_count=sum(
            1 for g in declined if g.get("decline_response") == "request_cake"
        ),
        total_gift_amount=sum(
            (Decimal(str(g.get("gift_amount") or 0)) for g in guests),
            Decimal("0"),
        ),
        arrived_count=sum(1 for g in guests if g.get("is_arrived")),
        undecided_count=sum(1 for g in guests if g.get("status") == "undecided"),
        invitation_pending_count=sum(
            1
            for g in guests
            if g.get("invitation_status") in {"pending_address", "pending_send"}
        ),
        cake_pending_count=sum(
            1
            for g in guests
            if g.get("cake_status") in {"pending_address", "pending_send"}
        ),
        unassigned_table_count=sum(
            (g.get("total_adults") or 0) + (g.get("total_children") or 0)
            for g in attending
            if not g.get("allocated_table")
        ),
    )


@router.get("/guests", response_model=list[GuestResponse])
def list_guests(
    q: str | None = Query(default=None, max_length=100),
    status_filter: GuestStatus | None = Query(default=None, alias="status"),
    shipping: ShippingFilter | None = Query(default=None),
    category: str | None = Query(default=None, max_length=100),
    table: str | None = Query(default=None, max_length=100),
    has_diet_notes: bool | None = Query(default=None),
    _admin: dict = Depends(get_current_admin),
) -> list[GuestResponse]:
    query = _active_guests_query().order("created_at")

    if q:
        search = _sanitize_search(q)
        if search:
            pattern = f"%{search}%"
            query = query.or_(
                "name.ilike.{0},phone.ilike.{0},guest_category.ilike.{0},"
                "allocated_table.ilike.{0},admin_notes.ilike.{0}".format(pattern)
            )

    if status_filter:
        query = query.eq("status", status_filter)
    if category:
        query = query.eq("guest_category", category.strip())
    if table:
        query = query.eq("allocated_table", table.strip())
    if has_diet_notes is True:
        query = query.filter("diet_notes", "not.is", "null")
    elif has_diet_notes is False:
        query = query.is_("diet_notes", "null")
    if shipping == "invitation":
        query = query.neq("invitation_status", "not_required")
    elif shipping == "cake":
        query = query.neq("cake_status", "not_required")
    elif shipping == "pending":
        query = query.or_(
            "invitation_status.in.(pending_address,pending_send),"
            "cake_status.in.(pending_address,pending_send)"
        )

    response = query.execute()
    return response.data or []


@router.post(
    "/guests",
    response_model=GuestResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_guest(
    payload: AdminGuestCreate,
    _admin: dict = Depends(get_current_admin),
) -> GuestResponse:
    data = payload.model_dump(mode="json")
    now = _utc_now()
    data["created_at"] = now
    data["updated_at"] = now

    response = get_supabase().table("guests").insert(data).execute()
    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create guest",
        )
    return response.data[0]


@router.patch("/guests/{guest_id}", response_model=GuestResponse)
def update_guest(
    guest_id: str,
    payload: AdminGuestUpdate,
    _admin: dict = Depends(get_current_admin),
) -> GuestResponse:
    updates = payload.model_dump(mode="json", exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    existing = _load_guest_or_404(guest_id)
    merged = {
        **existing,
        **updates,
    }
    validated = AdminGuestCreate.model_validate(merged)
    updates = validated.model_dump(mode="json")
    updates["updated_at"] = _utc_now()

    response = (
        get_supabase()
        .table("guests")
        .update(updates)
        .eq("id", guest_id)
        .is_("deleted_at", "null")
        .execute()
    )
    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Guest not found",
        )

    return response.data[0]


@router.delete("/guests/{guest_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_guest(
    guest_id: str,
    _admin: dict = Depends(get_current_admin),
) -> None:
    _load_guest_or_404(guest_id)
    now = _utc_now()

    response = (
        get_supabase()
        .table("guests")
        .update({"deleted_at": now, "updated_at": now})
        .eq("id", guest_id)
        .is_("deleted_at", "null")
        .execute()
    )
    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Guest not found",
        )
    return None
