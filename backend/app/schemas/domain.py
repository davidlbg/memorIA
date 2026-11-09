from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, field_validator

from app.schemas.usage import QueryCost

MemoryType = Literal["fact", "event", "intention", "commitment"]


class SourceMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    sender_contact_id: UUID | None
    sender_name_snapshot: str | None
    content: str
    message_type: str
    sent_at: datetime
    origin: str = "txt"
    revision: int = 1
    connector_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    content_updated_at: datetime | None = None
    external_message_id: str | None = None
    from_me: bool | None = None
    conversation_title: str | None = None
    retrieval_role: Literal["match", "context"] = "match"


class SupportingQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: UUID
    quote: str = Field(min_length=1, max_length=2000)


class SourceRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    revision: int = Field(ge=1, strict=True)


class SourceValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[SourceRevision] = Field(max_length=500)


class ExtractedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_type: MemoryType
    subject: str | None = Field(max_length=500)
    content: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    observed_at: datetime
    source_message_ids: list[UUID] = Field(min_length=1, max_length=40)
    evidence_summary: str = Field(min_length=1, max_length=2000)
    evidence_quote: str = Field(min_length=1, max_length=2000)
    evidence_basis: Literal["explicit", "interpretation"] = "explicit"
    supporting_quotes: list[SupportingQuote] = Field(default_factory=list, max_length=40)

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Timezone required")
        return value


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memories: list[ExtractedMemory] = Field(max_length=80)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["explicit_statement", "extracted_memory", "interpretation"]
    text: str = Field(min_length=1, max_length=2000)
    source_message_ids: list[UUID] = Field(min_length=1, max_length=10)
    quote: str = Field(min_length=1, max_length=2000)
    section: str | None = Field(default=None, max_length=80)
    supporting_quotes: list[SupportingQuote] = Field(default_factory=list, max_length=24)


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[Claim] = Field(max_length=10)


class Answer(GeneratedAnswer):
    insufficient_evidence: bool
    sources: list[SourceMessage]
    context_sources: list[SourceMessage] = Field(default_factory=list)
    provider: str
    clarification: str | None = None
    research_steps: list["ResearchStep"] = Field(default_factory=list)
    research_limited: bool = False
    response_mode: Literal["focused", "overview"] = "focused"
    context_revisions: list[SourceRevision] = Field(default_factory=list)
    coverage: list["ConversationCoverage"] = Field(default_factory=list)
    cost: "QueryCost | None" = None


class ConversationCoverage(BaseModel):
    conversation_id: UUID
    available_messages: int = Field(ge=0)
    inspected_messages: int = Field(ge=0)
    complete: bool


class ResearchStep(BaseModel):
    tool: str
    results: int = Field(ge=0)
    status: Literal["ok", "invalid_request", "limited"] = "ok"


class Query(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    contact_id: UUID | None = None
    conversation_id: UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    memory_type: MemoryType | None = None
    previous_question: str | None = Field(default=None, min_length=1, max_length=2000)
    timezone: str = Field(default="America/Sao_Paulo", max_length=64)
    compact_context: bool = False

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Timezone must be an IANA timezone") from None
        return value

    @field_validator("question")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question cannot be blank")
        return value.strip()


class ConversationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    conversation_type: str
    imported_at: datetime


class ContactView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    display_name: str


class ImportView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    filename: str
    source_kind: str = "txt"
    status: str
    total_messages: int
    imported_messages: int
    processed_messages: int
    attempts: int
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class MemoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    memory_type: MemoryType
    subject_contact_id: UUID | None
    content: str
    confidence: float
    status: str
    observed_at: datetime
    evidence_summary: str
    sources: list[SourceMessage]
    context_sources: list[SourceMessage] = Field(default_factory=list)
    evidence_basis: Literal["explicit", "interpretation"] = "explicit"
    supporting_quotes: list[SupportingQuote] = Field(default_factory=list)


class Deleted(BaseModel):
    deleted: bool = True


class ProviderSelection(BaseModel):
    llm_provider: Literal["mock", "openai", "ollama"]


class ConversationDetail(BaseModel):
    conversation: ConversationView
    messages: list[SourceMessage]


class ContactContext(BaseModel):
    contact: ContactView
    memories: list[MemoryView]


class HealthView(BaseModel):
    status: Literal["ok"] = "ok"
    llm_provider: Literal["mock", "openai", "ollama"]
    embedding_provider: Literal["mock", "openai", "ollama"]
    embedding_identity: str
    external_ai_enabled: bool
    import_max_bytes: int
    openai_available: bool
    ollama_available: bool = False
    llm_model: str = ""
    query_model: str = ""
    local_ai_enabled: bool = False
    openai_setup_available: bool = False
    ai_processing_since: datetime | None = None


class OpenAIConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    api_key: SecretStr = Field(min_length=20, max_length=2048)
    consent: Literal[True]

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.startswith("sk-") or any(char.isspace() for char in raw):
            raise ValueError("Use only the API key value")
        return value
