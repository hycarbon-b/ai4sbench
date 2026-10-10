from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class MailingDeliveryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipients: list[EmailStr] = Field(min_length=1, max_length=10)
    subject: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)
    html: str | None = Field(default=None, min_length=1, max_length=50_000)
    reply_to: EmailStr | None = None

    @field_validator("subject")
    @classmethod
    def strip_subject(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("subject must not be blank")
        return result

    @field_validator("text", "html")
    @classmethod
    def require_nonblank_body(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("mail body must not be blank")
        return value

    def delivery_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "recipients": [str(recipient) for recipient in self.recipients],
            "subject": self.subject,
            "text": self.text,
        }
        if self.html is not None:
            payload["html"] = self.html
        if self.reply_to is not None:
            payload["reply_to"] = [str(self.reply_to)]
        return payload


class MailingDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    state: Literal["pending", "sending", "completed", "failed"]
    attempts: int
    max_attempts: int
    available_at: datetime
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime
