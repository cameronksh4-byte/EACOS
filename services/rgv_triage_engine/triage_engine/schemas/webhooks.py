"""Inbound webhook payload models (WhatsApp Cloud API)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Lenient(BaseModel):
    """Meta adds fields over time; ignore unknown keys but validate known ones."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class WhatsAppMedia(_Lenient):
    """A document or image attachment reference."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[0-9A-Za-z_\-]+$")
    mime_type: str = Field(max_length=100)
    sha256: str | None = None
    filename: str | None = Field(default=None, max_length=255)
    caption: str | None = Field(default=None, max_length=3000)


class WhatsAppMessage(_Lenient):
    """One inbound message."""

    id: str = Field(min_length=1, max_length=256)
    from_: str = Field(alias="from", max_length=32)
    timestamp: str
    type: str
    document: WhatsAppMedia | None = None
    image: WhatsAppMedia | None = None

    @property
    def media(self) -> WhatsAppMedia | None:
        """The attachment, if this message carries one we process."""
        return self.document or self.image


class WhatsAppMetadata(_Lenient):
    """Identifies the receiving business phone number (routes to a tenant)."""

    phone_number_id: str = Field(max_length=64)
    display_phone_number: str | None = None


class WhatsAppValue(_Lenient):
    """``entry[].changes[].value``."""

    messaging_product: Literal["whatsapp"]
    metadata: WhatsAppMetadata
    messages: list[WhatsAppMessage] = Field(default_factory=list, max_length=100)


class WhatsAppChange(_Lenient):
    """``entry[].changes[]``."""

    field: str
    value: WhatsAppValue


class WhatsAppEntry(_Lenient):
    """``entry[]``."""

    id: str
    changes: list[WhatsAppChange] = Field(max_length=100)


class WhatsAppWebhookPayload(_Lenient):
    """Top-level WhatsApp Business Account webhook notification."""

    object: Literal["whatsapp_business_account"]
    entry: list[WhatsAppEntry] = Field(max_length=100)
