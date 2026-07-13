from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


GuestStatus = Literal["attend", "decline", "undecided"]
DeclineResponse = Literal["blessing_only", "request_cake"]
GuestCategory = Literal[
    "男方同事",
    "女方同事",
    "男方朋友",
    "女方朋友",
    "男方家人",
    "女方家人",
]
InvitationStatus = Literal[
    "not_required", "pending_address", "pending_send", "sent", "received"
]
CakeStatus = Literal[
    "not_required",
    "pending_pickup",
    "pending_address",
    "pending_send",
    "sent",
    "pickup",
]
ShippingFilter = Literal["invitation", "cake", "pending"]


class GuestBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=8, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    status: GuestStatus = "undecided"
    total_adults: int = Field(default=0, ge=0)
    total_children: int = Field(default=0, ge=0)
    actual_adults: int | None = Field(default=None, ge=0)
    actual_children: int | None = Field(default=None, ge=0)
    vegetarian_count: int = Field(default=0, ge=0)
    vegetarian_adults: int = Field(default=0, ge=0)
    vegetarian_children: int = Field(default=0, ge=0)
    allergy_notes: str | None = Field(default=None, max_length=500)
    child_seats: int = Field(default=0, ge=0)
    diet_notes: str | None = Field(default=None, max_length=500)
    need_invitation: bool = False
    invitation_address: str | None = Field(default=None, max_length=500)
    decline_response: DeclineResponse | None = None
    blessing_message: str | None = Field(default=None, max_length=1000)
    guest_category: GuestCategory | None = None
    invitation_status: InvitationStatus = "not_required"
    cake_status: CakeStatus = "not_required"
    shipping_recipient: str | None = Field(default=None, max_length=100)
    shipping_phone: str | None = Field(default=None, max_length=20)
    shipping_address: str | None = Field(default=None, max_length=500)
    shipping_date: date | None = None
    tracking_no: str | None = Field(default=None, max_length=100)
    is_arrived: bool = False
    arrived_at: str | None = None
    checkin_updated_at: str | None = None
    checkin_note: str | None = Field(default=None, max_length=500)
    gift_amount: Decimal = Field(default=Decimal("0"), ge=0)
    allocated_table: str | None = Field(default=None, max_length=100)
    admin_notes: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "phone")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        normalized = "".join(ch for ch in value.strip() if ch.isdigit() or ch == "+")
        if not normalized:
            raise ValueError("Phone is required")
        return normalized

    @field_validator("shipping_phone")
    @classmethod
    def normalize_optional_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = "".join(ch for ch in value.strip() if ch.isdigit() or ch == "+")
        return normalized or None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            return None
        if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
            raise ValueError("請填寫有效的 Email")
        return normalized

    @field_validator(
        "diet_notes",
        "allergy_notes",
        "invitation_address",
        "blessing_message",
        "shipping_recipient",
        "shipping_address",
        "tracking_no",
        "checkin_note",
        "allocated_table",
        "admin_notes",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def validate_guest_fields(self) -> "GuestBase":
        if self.status == "attend" and self.total_adults < 1:
            raise ValueError("Attending guests must include at least one adult")

        if self.status == "decline":
            self.total_adults = 0
            self.total_children = 0
            self.actual_adults = None
            self.actual_children = None
            self.vegetarian_count = 0
            self.vegetarian_adults = 0
            self.vegetarian_children = 0
            self.allergy_notes = None
            self.child_seats = 0
            self.diet_notes = None
            self.need_invitation = False
            self.invitation_address = None
            self.invitation_status = "not_required"
            if not self.decline_response:
                raise ValueError("無法出席時請選擇一個回覆選項")
        else:
            self.decline_response = None

        if self.need_invitation and not self.invitation_address:
            raise ValueError("需要喜帖時請填寫寄送地址")
        if not self.need_invitation:
            self.invitation_address = None
            self.invitation_status = "not_required"
        elif self.invitation_status == "not_required":
            self.invitation_status = "pending_send"

        if self.total_children <= 0:
            self.child_seats = 0
            self.vegetarian_children = 0
        elif self.child_seats > self.total_children:
            raise ValueError("兒童座椅數量不可超過小孩人數")

        total_guests = self.total_adults + self.total_children
        if self.vegetarian_count > total_guests:
            raise ValueError("素食人數不可超過總出席人數")
        self.vegetarian_adults = self.vegetarian_count
        self.vegetarian_children = 0

        if self.status == "attend":
            if self.cake_status not in {"pending_pickup", "pickup"}:
                self.cake_status = "pending_pickup"
            self.shipping_recipient = None
            self.shipping_phone = None
            self.shipping_address = None
        elif self.decline_response != "request_cake":
            self.cake_status = "not_required"
            self.shipping_recipient = None
            self.shipping_phone = None
            self.shipping_address = None
        elif not self.shipping_address:
            raise ValueError("希望收到喜餅時請填寫收件地址")
        elif not self.shipping_recipient:
            raise ValueError("希望收到喜餅時請填寫收件人")
        elif not self.shipping_phone:
            raise ValueError("希望收到喜餅時請填寫收件電話")
        elif self.cake_status == "not_required":
            self.cake_status = "pending_send"

        return self


class RsvpRequest(GuestBase):
    status: GuestStatus

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(*args, **kwargs)
        admin_only_fields = {
            "shipping_date",
            "tracking_no",
            "is_arrived",
            "actual_adults",
            "actual_children",
            "gift_amount",
            "arrived_at",
            "checkin_updated_at",
            "checkin_note",
            "allocated_table",
            "admin_notes",
        }
        for field in admin_only_fields:
            data.pop(field, None)
        data["invitation_status"] = (
            "pending_send" if data.get("need_invitation") else "not_required"
        )
        data["cake_status"] = (
            "pending_send"
            if data.get("decline_response") == "request_cake"
            else "pending_pickup"
            if data.get("status") == "attend"
            else "not_required"
        )
        if data.get("decline_response") == "request_cake":
            data["shipping_recipient"] = data.get("shipping_recipient") or data.get("name")
            data["shipping_phone"] = data.get("shipping_phone") or data.get("phone")
        else:
            data["shipping_address"] = None
            data["shipping_recipient"] = None
            data["shipping_phone"] = None
        return data


class AdminGuestCreate(GuestBase):
    pass


class AdminGuestUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, min_length=8, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    status: GuestStatus | None = None
    total_adults: int | None = Field(default=None, ge=0)
    total_children: int | None = Field(default=None, ge=0)
    actual_adults: int | None = Field(default=None, ge=0)
    actual_children: int | None = Field(default=None, ge=0)
    vegetarian_count: int | None = Field(default=None, ge=0)
    vegetarian_adults: int | None = Field(default=None, ge=0)
    vegetarian_children: int | None = Field(default=None, ge=0)
    allergy_notes: str | None = Field(default=None, max_length=500)
    child_seats: int | None = Field(default=None, ge=0)
    diet_notes: str | None = Field(default=None, max_length=500)
    need_invitation: bool | None = None
    invitation_address: str | None = Field(default=None, max_length=500)
    decline_response: DeclineResponse | None = None
    blessing_message: str | None = Field(default=None, max_length=1000)
    guest_category: GuestCategory | None = None
    invitation_status: InvitationStatus | None = None
    cake_status: CakeStatus | None = None
    shipping_recipient: str | None = Field(default=None, max_length=100)
    shipping_phone: str | None = Field(default=None, max_length=20)
    shipping_address: str | None = Field(default=None, max_length=500)
    shipping_date: date | None = None
    tracking_no: str | None = Field(default=None, max_length=100)
    is_arrived: bool | None = None
    arrived_at: str | None = None
    checkin_updated_at: str | None = None
    checkin_note: str | None = Field(default=None, max_length=500)
    gift_amount: Decimal | None = Field(default=None, ge=0)
    allocated_table: str | None = Field(default=None, max_length=100)
    admin_notes: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "phone")
    @classmethod
    def strip_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = "".join(ch for ch in value.strip() if ch.isdigit() or ch == "+")
        if not normalized:
            raise ValueError("Phone is required")
        return normalized

    @field_validator("shipping_phone")
    @classmethod
    def normalize_optional_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = "".join(ch for ch in value.strip() if ch.isdigit() or ch == "+")
        return normalized or None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            return None
        if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
            raise ValueError("請填寫有效的 Email")
        return normalized

    @field_validator(
        "diet_notes",
        "allergy_notes",
        "invitation_address",
        "blessing_message",
        "shipping_recipient",
        "shipping_address",
        "tracking_no",
        "checkin_note",
        "allocated_table",
        "admin_notes",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class GuestResponse(BaseModel):
    id: UUID
    name: str
    phone: str
    email: str | None = None
    status: GuestStatus
    total_adults: int
    total_children: int
    actual_adults: int | None = None
    actual_children: int | None = None
    vegetarian_count: int = 0
    vegetarian_adults: int = 0
    vegetarian_children: int = 0
    allergy_notes: str | None = None
    child_seats: int
    diet_notes: str | None
    need_invitation: bool
    invitation_address: str | None
    decline_response: DeclineResponse | None
    blessing_message: str | None
    guest_category: GuestCategory | None = None
    invitation_status: InvitationStatus = "not_required"
    cake_status: CakeStatus = "not_required"
    shipping_recipient: str | None = None
    shipping_phone: str | None = None
    shipping_address: str | None = None
    shipping_date: date | None = None
    tracking_no: str | None = None
    is_arrived: bool
    arrived_at: str | None = None
    checkin_updated_at: str | None = None
    checkin_note: str | None = None
    checkin_token: str | None = None
    checkin_token_rotated_at: str | None = None
    gift_amount: Decimal
    allocated_table: str | None
    admin_notes: str | None
    created_at: str
    updated_at: str | None = None
    deleted_at: str | None = None


class GuestCheckinUpdate(AdminGuestUpdate):
    is_arrived: bool | None = None
    actual_adults: int | None = Field(default=None, ge=0)
    actual_children: int | None = Field(default=None, ge=0)
    cake_status: CakeStatus | None = None
    checkin_note: str | None = Field(default=None, max_length=500)

    @field_validator("checkin_note")
    @classmethod
    def strip_checkin_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class TableSettingBase(BaseModel):
    table_name: str = Field(min_length=1, max_length=100)
    capacity: int = Field(default=12, ge=1, le=999)

    @field_validator("table_name")
    @classmethod
    def strip_table_name(cls, value: str) -> str:
        return value.strip()


class TableSettingUpsert(TableSettingBase):
    pass


class TableSettingRename(BaseModel):
    old_table_name: str = Field(min_length=1, max_length=100)
    new_table_name: str = Field(min_length=1, max_length=100)

    @field_validator("old_table_name", "new_table_name")
    @classmethod
    def strip_table_name(cls, value: str) -> str:
        return value.strip()


class TableSettingResponse(TableSettingBase):
    created_at: str | None = None
    updated_at: str | None = None


class AdminSummary(BaseModel):
    total_guests: int
    attending_households: int
    total_adults: int
    total_children: int
    total_attendees: int
    vegetarian_count: int
    invitation_count: int
    child_seats_count: int
    decline_blessing_only_count: int
    decline_request_cake_count: int
    total_gift_amount: Decimal
    arrived_count: int
    undecided_count: int = 0
    invitation_pending_count: int = 0
    cake_pending_count: int = 0
    unassigned_table_count: int = 0
