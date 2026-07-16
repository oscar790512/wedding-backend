import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.auth import get_current_admin
from app.config import settings
from app.database import get_supabase
from app.schemas.guest import (
    AdminGuestCreate,
    AdminGuestUpdate,
    AdminSummary,
    CronCounterIncrement,
    CronCounterResponse,
    GuestCheckinUpdate,
    GuestResponse,
    GuestStatus,
    ShippingFilter,
    TableSettingRename,
    TableSettingResponse,
    TableSettingUpsert,
)

router = APIRouter(prefix="/admin", tags=["admin"])

_SEARCH_UNSAFE = re.compile(r"[,().]")


def _sanitize_search(value: str) -> str:
    return _SEARCH_UNSAFE.sub("", value.strip())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_checkin_token() -> str:
    return secrets.token_urlsafe(24)


def _create_unique_checkin_token() -> str:
    return _generate_checkin_token()


def _active_guests_query():
    return get_supabase().table("guests").select("*").is_("deleted_at", "null")


def _verify_cron_counter_secret(secret: str | None) -> None:
    configured_secret = settings.cron_counter_secret.strip()
    if not configured_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cron counter secret is not configured",
        )

    if not secret or not secrets.compare_digest(secret, configured_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid cron counter secret",
        )


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


def _load_guest_by_checkin_token_or_404(token: str) -> dict:
    response = (
        get_supabase()
        .table("guests")
        .select("*")
        .eq("checkin_token", token)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    )
    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Check-in QR Code not found",
        )
    return response.data[0]


def _guest_planned_seat_count(guest: dict) -> int:
    return int(guest.get("total_adults") or 0) + int(guest.get("total_children") or 0)


def _ensure_table_capacity(
    supabase,
    guest_id: str,
    guest: dict,
    table_name: str | None,
) -> None:
    if not table_name or guest.get("status") != "attend":
        return

    setting_response = (
        supabase.table("table_settings")
        .select("table_name,capacity")
        .eq("table_name", table_name)
        .limit(1)
        .execute()
    )
    if not setting_response.data:
        return

    capacity = int(setting_response.data[0].get("capacity") or 0)
    response = (
        supabase.table("guests")
        .select("id,status,total_adults,total_children")
        .eq("allocated_table", table_name)
        .is_("deleted_at", "null")
        .execute()
    )
    seated_count = sum(
        _guest_planned_seat_count(seated_guest)
        for seated_guest in response.data or []
        if seated_guest.get("status") == "attend" and seated_guest.get("id") != guest_id
    )
    requested_count = _guest_planned_seat_count(guest)

    if seated_count + requested_count > capacity:
        remaining = max(capacity - seated_count, 0)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{table_name} 剩餘 {remaining} 位，"
                f"無法安排 {guest.get('name') or '此賓客'}（{requested_count} 位）。"
            ),
        )


@router.post("/cron-counter/increment", response_model=CronCounterResponse)
def increment_cron_counter(
    payload: CronCounterIncrement,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> CronCounterResponse:
    _verify_cron_counter_secret(x_cron_secret)

    response = get_supabase().rpc(
        "increment_api_counter",
        {
            "counter_name": payload.counter_key,
            "increment_by": payload.amount,
        },
    ).execute()
    data = response.data or []
    if not data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to increment cron counter",
        )

    return data[0]


@router.get("/table-settings", response_model=list[TableSettingResponse])
def list_table_settings(
    _admin: dict = Depends(get_current_admin),
) -> list[TableSettingResponse]:
    response = (
        get_supabase()
        .table("table_settings")
        .select("table_name,capacity,created_at,updated_at")
        .order("created_at")
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


@router.patch("/table-settings/rename", response_model=TableSettingResponse)
def rename_table_setting(
    payload: TableSettingRename,
    _admin: dict = Depends(get_current_admin),
) -> TableSettingResponse:
    if payload.old_table_name == "主桌" and payload.new_table_name != "主桌":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Main table name cannot be changed",
        )

    if payload.old_table_name == payload.new_table_name:
        response = (
            get_supabase()
            .table("table_settings")
            .select("table_name,capacity,created_at,updated_at")
            .eq("table_name", payload.old_table_name)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Table setting not found",
        )

    supabase = get_supabase()
    existing = (
        supabase.table("table_settings")
        .select("table_name")
        .eq("table_name", payload.new_table_name)
        .limit(1)
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Table name already exists",
        )

    updated_at = _utc_now()
    current = (
        supabase.table("table_settings")
        .select("table_name,capacity,created_at,updated_at")
        .eq("table_name", payload.old_table_name)
        .limit(1)
        .execute()
    )

    if not current.data:
        setting_response = (
            supabase.table("table_settings")
            .insert(
                {
                    "table_name": payload.new_table_name,
                    "capacity": 12,
                    "updated_at": updated_at,
                }
            )
            .execute()
        )
    else:
        setting_response = (
            supabase.table("table_settings")
            .update({"table_name": payload.new_table_name, "updated_at": updated_at})
            .eq("table_name", payload.old_table_name)
            .execute()
        )

    if not setting_response.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rename table setting",
        )

    supabase.table("guests").update(
        {"allocated_table": payload.new_table_name, "updated_at": updated_at}
    ).eq("allocated_table", payload.old_table_name).is_(
        "deleted_at", "null"
    ).execute()

    return setting_response.data[0]


@router.delete("/table-settings", status_code=status.HTTP_204_NO_CONTENT)
def delete_table_setting(
    table_name: str = Query(min_length=1, max_length=100),
    _admin: dict = Depends(get_current_admin),
) -> None:
    response = (
        get_supabase()
        .table("table_settings")
        .delete()
        .eq("table_name", table_name.strip())
        .execute()
    )
    if response.data is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete table setting",
        )
    return None


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
            (g.get("vegetarian_count") or 0)
            or (g.get("vegetarian_adults") or 0)
            + (g.get("vegetarian_children") or 0)
            for g in attending
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
                "name.ilike.{0},phone.ilike.{0},email.ilike.{0},"
                "guest_category.ilike.{0},allocated_table.ilike.{0},"
                "admin_notes.ilike.{0}".format(pattern)
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
        query = query.eq("decline_response", "request_cake")
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
    if data.get("status") == "attend":
        data["checkin_token"] = _create_unique_checkin_token()
        data["checkin_token_rotated_at"] = now

    response = get_supabase().table("guests").insert(data).execute()
    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create guest",
        )
    return response.data[0]


@router.get("/checkin/{token}", response_model=GuestResponse)
def get_guest_by_checkin_token(
    token: str,
    _admin: dict = Depends(get_current_admin),
) -> GuestResponse:
    return _load_guest_by_checkin_token_or_404(token)


@router.post("/guests/{guest_id}/checkin-token/reset", response_model=GuestResponse)
def reset_guest_checkin_token(
    guest_id: str,
    _admin: dict = Depends(get_current_admin),
) -> GuestResponse:
    _load_guest_or_404(guest_id)
    now = _utc_now()
    response = (
        get_supabase()
        .table("guests")
        .update(
            {
                "checkin_token": _create_unique_checkin_token(),
                "checkin_token_rotated_at": now,
                "updated_at": now,
            }
        )
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


@router.patch("/guests/{guest_id}/checkin", response_model=GuestResponse)
def update_guest_checkin(
    guest_id: str,
    payload: GuestCheckinUpdate,
    _admin: dict = Depends(get_current_admin),
) -> GuestResponse:
    updates = payload.model_dump(mode="json", exclude_unset=True)
    allowed_fields = {
        "is_arrived",
        "actual_adults",
        "actual_children",
        "cake_status",
        "checkin_note",
        "allocated_table",
    }
    updates = {key: value for key, value in updates.items() if key in allowed_fields}
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    existing = _load_guest_or_404(guest_id)
    if updates.get("is_arrived") is True and existing.get("status") != "attend":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only attending guests can be checked in",
        )

    now = _utc_now()
    if "is_arrived" in updates:
        if updates["is_arrived"]:
            updates["arrived_at"] = existing.get("arrived_at") or now
        else:
            updates["arrived_at"] = None
            updates["actual_adults"] = None
            updates["actual_children"] = None

    merged = {
        **existing,
        **updates,
    }
    _ensure_table_capacity(
        get_supabase(),
        guest_id,
        merged,
        merged.get("allocated_table"),
    )

    updates["checkin_updated_at"] = now
    updates["updated_at"] = now

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
    _ensure_table_capacity(
        get_supabase(),
        guest_id,
        updates,
        updates.get("allocated_table"),
    )
    updates["updated_at"] = _utc_now()
    if (
        updates.get("status") == "attend"
        and not existing.get("checkin_token")
    ):
        updates["checkin_token"] = _create_unique_checkin_token()
        updates["checkin_token_rotated_at"] = updates["updated_at"]

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
