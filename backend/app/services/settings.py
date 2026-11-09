from typing import Literal

from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.integrations.providers import ProviderError
from app.models.entities import AIConfiguration, RuntimeSetting, now
from app.security.ai_credentials import decrypt_key, encrypt_key


def runtime_settings(session: Session) -> Settings:
    base = get_settings()
    # One statement snapshot prevents mixing a rotated key with an older state.
    anchor = select(literal(1).label("singleton")).subquery()
    configuration, choice = session.execute(
        select(AIConfiguration, RuntimeSetting).select_from(anchor)
        .outerjoin(AIConfiguration, AIConfiguration.provider == "openai")
        .outerjoin(RuntimeSetting, RuntimeSetting.key == "llm_provider")
        .execution_options(populate_existing=True)
    ).one()
    if configuration is not None:
        values = base.model_dump()
        values.update(local_ai_enabled=False, external_ai_enabled=configuration.enabled)
        if configuration.enabled:
            if configuration.encrypted_key is None:
                raise ProviderError("Saved AI credential is missing")
            values.update(
                openai_api_key=decrypt_key(base, configuration.encrypted_key),
                llm_provider=choice.value if choice else "openai",
                embedding_provider="openai",
                llm_model="gpt-4.1-mini",
                embedding_model="text-embedding-3-small",
                ai_processing_since=configuration.processing_since,
                ai_background_enabled=True,
            )
        else:
            values.update(llm_provider="mock", embedding_provider="mock", openai_api_key=SecretStr(""), ai_background_enabled=False)
        try:
            return Settings.model_validate(values)
        except ValidationError:
            raise ProviderError("Saved AI configuration is unavailable") from None
    if choice is None:
        return base
    values = base.model_dump()
    values["llm_provider"] = choice.value
    try:
        return Settings.model_validate(values)
    except ValidationError:
        raise ProviderError(
            "Saved provider configuration is unavailable; select mock or restore the explicit provider configuration"
        ) from None


def select_provider(session: Session, provider: Literal["mock", "openai", "ollama"]) -> None:
    base = runtime_settings(session)
    if provider == "openai" and (
        not base.external_ai_enabled or not base.openai_api_key.get_secret_value()
    ):
        raise HTTPException(
            422, "Configure a chave OpenAI e EXTERNAL_AI_ENABLED no backend antes de habilitar."
        )
    if provider == "ollama" and (not base.local_ai_enabled or not base.ollama_llm_digest):
        raise HTTPException(
            422, "Configure IA local e os modelos instalados no backend antes de habilitar."
        )
    session.execute(
        insert(RuntimeSetting)
        .values(key="llm_provider", value=provider)
        .on_conflict_do_update(index_elements=["key"], set_={"value": provider})
    )


def save_openai(session: Session, secret: SecretStr) -> None:
    encrypted = encrypt_key(get_settings(), secret)
    session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("ai-configuration", 0))))
    record = session.get(AIConfiguration, "openai", populate_existing=True)
    if record is None:
        record = AIConfiguration(provider="openai", processing_since=now())
        session.add(record)
    record.enabled = True
    record.encrypted_key = encrypted
    session.flush()
    session.execute(
        insert(RuntimeSetting).values(key="llm_provider", value="openai")
        .on_conflict_do_update(index_elements=["key"], set_={"value": "openai"})
    )


def remove_openai(session: Session) -> None:
    session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("ai-configuration", 0))))
    record = session.get(AIConfiguration, "openai", populate_existing=True)
    if record is None:
        record = AIConfiguration(provider="openai", processing_since=now())
        session.add(record)
    record.enabled = False
    record.encrypted_key = None
    session.execute(
        insert(RuntimeSetting).values(key="llm_provider", value="mock")
        .on_conflict_do_update(index_elements=["key"], set_={"value": "mock"})
    )
