from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


GuestStatus = Literal["attend", "decline", "undecided"]


class RsvpRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=8, max_length=20)
    status: GuestStatus
    total_adults: int = Field(default=0, ge=0)
    total_children: int = Field(default=0, ge=0)
    diet_notes: str | None = Field(default=None, max_length=500)
    need_invitation: bool = False
    invitation_address: str | None = Field(default=None, max_length=500)
    blessing_message: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "phone")
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        return value.strip()

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return "".join(ch for ch in value if ch.isdigit() or ch == "+")

    @field_validator("invitation_address")
    @classmethod
    def strip_invitation_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def validate_invitation_address(self) -> "RsvpRequest":
        if self.need_invitation and not self.invitation_address:
            raise ValueError("需要喜帖時請填寫寄送地址")
        if not self.need_invitation:
            self.invitation_address = None
        return self


class GuestResponse(BaseModel):
    id: UUID
    name: str
    phone: str
    status: GuestStatus
    total_adults: int
    total_children: int
    diet_notes: str | None
    need_invitation: bool
    invitation_address: str | None
    blessing_message: str | None
    is_arrived: bool
    gift_amount: Decimal
    allocated_table: str | None
    admin_notes: str | None
    created_at: str


class GuestCheckinUpdate(BaseModel):
    is_arrived: bool | None = None
    gift_amount: Decimal | None = Field(default=None, ge=0)


class AdminSummary(BaseModel):
    total_guests: int
    attending_households: int
    total_adults: int
    total_children: int
    total_attendees: int
    vegetarian_count: int
    invitation_count: int
    total_gift_amount: Decimal
    arrived_count: int
