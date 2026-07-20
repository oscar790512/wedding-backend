import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


USERNAME_PATTERN = re.compile(r"^[a-z0-9._-]{3,32}$")


def normalize_username(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("帳號必須是文字")
    username = value.strip().lower()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(
            "帳號須為 3–32 個英文字母、數字、句點、底線或連字號",
        )
    return username


def normalize_display_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("顯示名稱必須是文字")
    display_name = value.strip()
    if not display_name:
        raise ValueError("顯示名稱不可空白")
    return display_name


class StaffUserCreate(BaseModel):
    username: str
    display_name: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_username(value)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return normalize_display_name(value)


class StaffDisplayNameUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=50)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return normalize_display_name(value)


class StaffPasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class StaffStatusUpdate(BaseModel):
    is_active: bool


class StaffUserResponse(BaseModel):
    username: str
    display_name: str
    role: Literal["staff"]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class StaffAuditLogResponse(BaseModel):
    actor_username: str
    target_username: str
    action: Literal[
        "created",
        "display_name_updated",
        "password_reset",
        "deactivated",
        "reactivated",
    ]
    created_at: datetime
