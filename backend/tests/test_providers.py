import math
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.integrations.providers import (
    MockEmbeddingProvider,
    MockLLMProvider,
    ProviderError,
    validate_vectors,
)
from app.schemas.domain import Extraction, SourceMessage


def test_external_provider_requires_explicit_consent() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="openai", external_ai_enabled=False)


async def test_mock_embeddings_deterministic_and_not_compatible_across_dimensions() -> None:
    vectors = await MockEmbeddingProvider(256).embed_texts(
        ["documento sexta", "documento sexta", "", "férias"]
    )
    assert vectors[0] == vectors[1]
    assert vectors[0] != vectors[3]
    assert math.isclose(sum(v * v for v in vectors[0]), 1)
    assert all(v == 0 for v in vectors[2])
    with pytest.raises(ProviderError):
        validate_vectors(vectors, 4, 512)


async def test_mock_does_not_confirm_questions_or_hypotheticals() -> None:
    messages = [
        SourceMessage(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_contact_id=None,
            sender_name_snapshot="Alice",
            content=text,
            message_type="text",
            sent_at=datetime.now(UTC),
        )
        for text in [
            "Eu trabalho como engenheira.",
            "Eu sou astronauta?",
            "Talvez eu planejo mudar.",
            "Se eu trabalho como médica…",
            "Eu prometo enviar o documento.",
        ]
    ]
    result = await MockLLMProvider().extract_memories(messages)
    assert [m.memory_type for m in result.memories] == ["fact", "commitment"]
    assert [m.evidence_quote for m in result.memories] == [messages[0].content, messages[4].content]


def test_extraction_rejects_unknown_types_and_unbounded_confidence() -> None:
    with pytest.raises(ValidationError):
        Extraction.model_validate(
            {"memories": [{"memory_type": "wish", "content": "invented", "confidence": 2}]}
        )
