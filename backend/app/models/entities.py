import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class AIConfiguration(Base):
    __tablename__ = "ai_configuration"
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    encrypted_key: Mapped[str | None] = mapped_column(Text)
    processing_since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("provider = 'openai'", name="ck_ai_provider"),
        CheckConstraint(
            "(enabled AND encrypted_key IS NOT NULL) OR (NOT enabled AND encrypted_key IS NULL)",
            name="ck_ai_key_state",
        ),
    )


class Identity:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AIBudgetPeriod(Base):
    __tablename__ = "ai_budget_periods"
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    limit_micro_usd: Mapped[int] = mapped_column(BigInteger)
    prior_micro_usd: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("limit_micro_usd >= 0 AND (prior_micro_usd IS NULL OR prior_micro_usd >= 0)"),
    )


class AIUsage(Identity, Base):
    __tablename__ = "ai_usage"
    month: Mapped[str] = mapped_column(ForeignKey("ai_budget_periods.month"), index=True)
    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    model_name: Mapped[str] = mapped_column(String(100))
    endpoint: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(16), default="reserved")
    reserved_micro_usd: Mapped[int] = mapped_column(BigInteger)
    cost_micro_usd: Mapped[int | None] = mapped_column(BigInteger)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cached_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_write_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger)
    pricing_version: Mapped[str] = mapped_column(String(30), default="openai-standard-2026-11-06")
    __table_args__ = (
        CheckConstraint("status IN ('reserved','settled','uncertain','rejected')"),
        CheckConstraint("reserved_micro_usd >= 0 AND (cost_micro_usd IS NULL OR cost_micro_usd >= 0)"),
        CheckConstraint("(status = 'settled' AND cost_micro_usd IS NOT NULL) OR (status != 'settled' AND cost_micro_usd IS NULL)"),
    )


class Conversation(Identity, Base):
    __tablename__ = "conversations"
    external_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    conversation_type: Mapped[str] = mapped_column(String(16), default="unknown")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (CheckConstraint("conversation_type IN ('direct','group','unknown')"),)


class Contact(Identity, Base):
    __tablename__ = "contacts"
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(500))
    normalized_name: Mapped[str] = mapped_column(String(500))
    external_identifier: Mapped[str | None] = mapped_column(String(200))
    external_alias: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("conversation_id", "normalized_name"),)


class ImportJob(Identity, Base):
    __tablename__ = "import_jobs"
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(250))
    source_kind: Mapped[str] = mapped_column(String(30), default="txt", server_default="txt")
    processing_identity: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    total_messages: Mapped[int] = mapped_column(Integer)
    imported_messages: Mapped[int] = mapped_column(Integer, default=0)
    processed_messages: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(500))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("status IN ('pending','processing','completed','failed')"),)


class Message(Identity, Base):
    __tablename__ = "messages"
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )
    sender_name_snapshot: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(30))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    external_message_id: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(30), default="txt", server_default="txt")
    delivery_state: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active"
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    connector_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    __table_args__ = (
        UniqueConstraint("conversation_id", "content_hash"),
        Index("ix_messages_context_order", "conversation_id", "sent_at", "sequence", "id"),
    )

    @property
    def content_updated_at(self) -> datetime | None:
        value = self.connector_metadata.get("edited_at")
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Invalid native revision timestamp")
        return datetime.fromisoformat(value)

    @property
    def from_me(self) -> bool | None:
        value = self.connector_metadata.get("from_me")
        if value is None:
            return None
        if not isinstance(value, bool):
            raise ValueError("Invalid native direction")
        return value


class Memory(Identity, Base):
    __tablename__ = "memories"
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    memory_type: Mapped[str] = mapped_column(String(20), index=True)
    subject_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    normalized_content: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="active")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    evidence_summary: Mapped[str] = mapped_column(Text)
    evidence_basis: Mapped[str] = mapped_column(
        String(20), default="explicit", server_default="explicit"
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (
        UniqueConstraint("conversation_id", "fingerprint"),
        CheckConstraint("memory_type IN ('fact','event','intention','commitment')"),
        CheckConstraint("status IN ('active','outdated','disputed','deleted')"),
        CheckConstraint("confidence >= 0 AND confidence <= 1"),
        CheckConstraint(
            "evidence_basis IN ('explicit','interpretation')", name="ck_memory_evidence_basis"
        ),
    )


class MemorySource(Identity, Base):
    __tablename__ = "memory_sources"
    memory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    relevance_score: Mapped[float] = mapped_column(Float, default=1)
    evidence_quote: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (UniqueConstraint("memory_id", "message_id"),)


class MemoryContext(Base):
    __tablename__ = "memory_context"
    memory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("revision > 0", name="ck_memory_context_revision"),)


class MessageEmbedding(Identity, Base):
    __tablename__ = "message_embeddings"
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dimensions))
    model_name: Mapped[str] = mapped_column(String(200))
    __table_args__ = (UniqueConstraint("message_id", "model_name"),)


class MemoryEmbedding(Identity, Base):
    __tablename__ = "memory_embeddings"
    memory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embedding_dimensions))
    model_name: Mapped[str] = mapped_column(String(200))
    __table_args__ = (UniqueConstraint("memory_id", "model_name"),)


for entity in (Message, Memory):
    Base.metadata.tables[entity.__tablename__].append_constraint(
        Index(
            f"ix_{entity.__tablename__}_fts",
            func.to_tsvector(literal_column("'simple'::regconfig"), entity.content),
            postgresql_using="gin",
        )
    )


class WhatsAppConnection(Identity, Base):
    __tablename__ = "whatsapp_connections"
    slot: Mapped[str] = mapped_column(String(30), unique=True, default="local")
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    fence: Mapped[int] = mapped_column(Integer, default=1)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30), default="DISCONNECTED")
    account_id: Mapped[str | None] = mapped_column(String(64))
    last_status_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    captured_messages: Mapped[int] = mapped_column(BigInteger, default=0)
    duplicate_events: Mapped[int] = mapped_column(BigInteger, default=0)
    ignored_events: Mapped[int] = mapped_column(BigInteger, default=0)


class WhatsAppChat(Identity, Base):
    __tablename__ = "whatsapp_chats"
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )


class WhatsAppChatAlias(Identity, Base):
    __tablename__ = "whatsapp_chat_aliases"
    account_id: Mapped[str] = mapped_column(String(64))
    jid: Mapped[str] = mapped_column(String(180))
    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("whatsapp_chats.id", ondelete="CASCADE"), index=True
    )
    __table_args__ = (UniqueConstraint("account_id", "jid"),)


class WhatsAppMessageState(Identity, Base):
    __tablename__ = "whatsapp_message_states"
    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("whatsapp_chats.id", ondelete="CASCADE"), index=True
    )
    external_message_id: Mapped[str] = mapped_column(String(200))
    from_me: Mapped[bool] = mapped_column(Boolean)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )
    last_version_ms: Mapped[int | None] = mapped_column(BigInteger)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_locally: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("chat_id", "external_message_id", "from_me"),)


class WhatsAppEventRecord(Identity, Base):
    __tablename__ = "whatsapp_events"
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    message_state_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("whatsapp_message_states.id", ondelete="CASCADE"), index=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    outcome: Mapped[str] = mapped_column(String(30), default="pending", index=True)


class MessageWorkItem(Identity, Base):
    __tablename__ = "message_work_items"
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("job_id", "message_id"),)
