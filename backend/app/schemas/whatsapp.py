from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

State = Literal[
    "DISCONNECTED",
    "INITIALIZING",
    "QR_REQUIRED",
    "CONNECTING",
    "CONNECTED",
    "RECONNECTING",
    "LOGGED_OUT",
    "ERROR",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def reject_nul(cls, value: object) -> object:
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str) and "\x00" in item:
                raise ValueError("NUL is not supported by PostgreSQL text/JSONB")
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        return value


class Lease(StrictModel):
    run_id: UUID
    fence: int = Field(ge=1)


class ClaimLease(StrictModel):
    instance_id: UUID
    run_id: UUID


class ConnectionUpdate(Lease):
    state: State
    account_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class NativeContact(StrictModel):
    display_name: str | None = Field(default=None, max_length=500)
    vcard: str | None = Field(default=None, max_length=8000)


class MessageMetadata(StrictModel):
    native_type: str = Field(max_length=100)
    caption: str | None = Field(default=None, max_length=20000)
    mimetype: str | None = Field(default=None, max_length=200)
    filename: str | None = Field(default=None, max_length=500)
    file_size: int | None = Field(default=None, ge=0, le=9007199254740991)
    file_sha256: str | None = Field(default=None, max_length=128)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    location_name: str | None = Field(default=None, max_length=500)
    address: str | None = Field(default=None, max_length=2000)
    contacts: list[NativeContact] | None = Field(default=None, max_length=20)
    quoted_message_id: str | None = Field(default=None, max_length=200)
    quoted_participant: str | None = Field(default=None, max_length=180)
    stub_type: int | None = None
    stub_parameters: list[str] | None = Field(default=None, max_length=20)

    @field_validator("stub_parameters")
    @classmethod
    def bounded_parameters(cls, value: list[str] | None) -> list[str] | None:
        if value and any(len(item) > 1000 for item in value):
            raise ValueError("Oversized stub parameter")
        return value


class MessageEvent(StrictModel):
    event_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    account_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_type: Literal["message.upsert", "message.edit", "message.revoke"]
    chat_jid: str = Field(max_length=180, pattern=r"^\d+(?:-\d+)?@(s\.whatsapp\.net|lid|g\.us)$")
    chat_jid_alt: str | None = Field(
        default=None, max_length=180, pattern=r"^\d+(?:-\d+)?@(s\.whatsapp\.net|lid|g\.us)$"
    )
    message_id: str = Field(min_length=1, max_length=200, pattern=r"^[\x21-\x7e]+$")
    sender_jid: str | None = Field(
        default=None, max_length=180, pattern=r"^\d+@(s\.whatsapp\.net|lid)$"
    )
    sender_jid_alt: str | None = Field(
        default=None, max_length=180, pattern=r"^\d+@(s\.whatsapp\.net|lid)$"
    )
    sender_name: str | None = Field(default=None, min_length=1, max_length=500)
    from_me: bool = Field(strict=True)
    sent_at: str | None = None
    version_ms: int | None = Field(ge=0, le=253402300799999)
    message_type: Literal["text", "media_reference", "location", "contact", "system", "unsupported"]
    text: str = Field(max_length=20000)
    origin: Literal["whatsapp_realtime", "whatsapp_history", "whatsapp_reconnect"]
    metadata: MessageMetadata

    @field_validator("sent_at")
    @classmethod
    def aware_timestamp(cls, value: str | None) -> str | None:
        if value:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Timestamp must include timezone")
        return value

    @model_validator(mode="after")
    def required_fields(self) -> "MessageEvent":
        if self.event_type == "message.upsert" and (not self.sent_at or not self.sender_jid):
            raise ValueError("Upsert requires original timestamp and sender")
        if self.event_type != "message.revoke" and self.version_ms is None:
            raise ValueError("Native version required")
        if self.event_type == "message.revoke" and self.text:
            raise ValueError("Revoke contains no text")
        return self


class Delivery(StrictModel):
    sequence: int = Field(ge=1, le=9007199254740991)
    event: MessageEvent


class EventBatch(Lease):
    items: list[Delivery] = Field(min_length=1, max_length=25)


class DeliveryResult(StrictModel):
    accepted: int
    duplicates: int
    ignored: int
    last_sequence: int


class SyncCheckpoint(Lease):
    sequence: int = Field(ge=0, le=9007199254740991)


class PublicConnectorStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credentials_present: bool | None = None
    connection_requested: bool | None = None
    available: bool
    network_enabled: bool
    state: State
    account_id: str | None
    qr_data_url: str | None
    qr_expires_at: str | None
    last_status_at: str
    metrics: dict[str, JsonValue]
    history_notice: str
    error_code: str | None
