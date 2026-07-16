from datetime import date

from pydantic import BaseModel


class RsvpSettingsResponse(BaseModel):
    rsvp_deadline: date | None = None
    updated_at: str | None = None


class RsvpSettingsUpdate(BaseModel):
    rsvp_deadline: date | None = None
