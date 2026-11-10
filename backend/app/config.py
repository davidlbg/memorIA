from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WhatsAppChatAliasGroup(BaseModel):
    """An operator-approved native PN/LID equivalence, scoped to one account."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    account_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    pn_jid: str = Field(max_length=180, pattern=r"^\d+@s\.whatsapp\.net$")
    lid_jid: str = Field(max_length=180, pattern=r"^\d+@lid$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.connector"), extra="ignore", hide_input_in_errors=True
    )
    database_url: str = "postgresql+psycopg://memory:memory@localhost:5432/memory"
    auth_secret: SecretStr = SecretStr("")
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    llm_provider: Literal["mock", "openai", "ollama"] = "mock"
    embedding_provider: Literal["mock", "openai", "ollama"] = "mock"
    local_ai_enabled: bool = False
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_llm_digest: str = ""
    ollama_embedding_digest: str = ""
    local_ai_context_characters: int = Field(default=8000, ge=8000, le=32000)
    local_ai_timeout_seconds: float = Field(default=300, gt=0, le=600)
    local_ai_max_output_tokens: int = Field(default=3072, ge=128, le=8192)
    external_ai_enabled: bool = False
    openai_api_key: SecretStr = SecretStr("")
    openai_reasoning_effort: Literal["low", "medium", "high"] = "low"
    openai_query_model: Literal["gpt-4.1-mini", "gpt-4.1", "gpt-5.4-mini", "gpt-5.4-mini-2026-03-17", "gpt-5.4", "gpt-5.4-2026-03-05", "gpt-6.1-sol"] | None = None
    ai_credentials_key_file: str = ""
    ai_processing_since: datetime | None = None
    ai_background_enabled: bool = True
    ai_monthly_budget_usd: Decimal | None = Field(default=None, ge=0, le=1000000)
    ai_budget_prior_spend_usd: Decimal = Field(default=Decimal("0"), ge=0, le=1000000)
    ai_budget_prior_spend_month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    ai_usage_role: Literal["queries", "memories", "indexing", "setup"] = "queries"
    worker_indexes_messages: bool = True
    research_max_steps: int = Field(default=8, ge=2, le=12)
    research_timeout_seconds: float = Field(default=90, ge=10, le=180)
    llm_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=256, ge=32, le=2000)
    provider_timeout_seconds: float = Field(default=45, gt=0, le=120)
    import_max_bytes: int = Field(default=5_000_000, gt=0, le=50_000_000)
    log_level: str = "INFO"
    extension_origin: str = ""
    worker_poll_seconds: float = Field(default=2, gt=0)
    worker_batch_messages: int = Field(default=20, ge=1, le=20)
    connector_secret: SecretStr = SecretStr("")
    connector_url: str = "http://127.0.0.1:3001"
    connector_max_events: int = Field(default=500000, ge=100, le=5000000)
    connector_max_pending: int = Field(default=5000, ge=10, le=50000)
    whatsapp_chat_alias_groups: list[WhatsAppChatAliasGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_chat_alias_groups(self) -> "Settings":
        seen: set[tuple[str, str]] = set()
        for group in self.whatsapp_chat_alias_groups:
            for jid in (group.pn_jid, group.lid_jid):
                key = (group.account_id, jid)
                if key in seen:
                    raise ValueError("WhatsApp alias groups must be disjoint within each account")
                seen.add(key)
        return self

    @model_validator(mode="after")
    def check_providers(self) -> "Settings":
        if self.llm_provider == "openai" or self.embedding_provider == "openai":
            if not self.external_ai_enabled or not self.openai_api_key.get_secret_value():
                raise ValueError("OpenAI requires EXTERNAL_AI_ENABLED=true and OPENAI_API_KEY")
        if "ollama" in (self.llm_provider, self.embedding_provider):
            parsed = urlparse(self.ollama_url)
            if (
                not self.local_ai_enabled
                or parsed.scheme != "http"
                or parsed.hostname not in {"localhost", "127.0.0.1", "ollama"}
                or parsed.port != 11434
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "Local AI requires explicit consent and a local Ollama origin on port 11434"
                )
            import re

            for provider, model, digest in (
                (self.llm_provider, self.llm_model, self.ollama_llm_digest),
                (self.embedding_provider, self.embedding_model, self.ollama_embedding_digest),
            ):
                if provider == "ollama" and (
                    "cloud" in model.casefold() or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
                ):
                    raise ValueError(
                        "Configure an installed local model and its full SHA256 digest"
                    )
        return self

    @property
    def embedding_identity(self) -> str:
        model = "hash-v1" if self.embedding_provider == "mock" else self.embedding_model
        if self.embedding_provider == "ollama":
            model += "@" + self.ollama_embedding_digest
        return f"{self.embedding_provider}:{model}:{self.embedding_dimensions}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
