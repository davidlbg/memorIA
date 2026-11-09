import asyncio
import json
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.ingestion.parser import parse_export
from app.core.memory.pipeline import process_batch
from app.integrations.local import ExtractionInput, compact_inputs
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider
from app.models.entities import Message
from app.schemas.domain import Extraction, GeneratedAnswer, Query, SourceMessage
from app.services.imports import commit_import
from app.services.knowledge import answer_query
from app.services.reprocessing import processing_identity


def local_settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="ollama",
        local_ai_enabled=True,
        llm_model="fixture:local",
        ollama_llm_digest="sha256:" + "a" * 64,
        embedding_provider="mock",
        embedding_dimensions=256,
        local_ai_context_characters=8000,
    )


class CaptureProvider(MockLLMProvider):
    def __init__(self) -> None:
        self.inputs: list[SourceMessage] = []

    async def extract_memories(self, messages: list[SourceMessage]) -> Extraction:
        self.inputs = messages
        return Extraction(memories=[])

    async def generate(self, prompt: str, context: list[SourceMessage]) -> GeneratedAnswer:
        self.inputs = context
        return GeneratedAnswer(claims=[])


@pytest.mark.database
@pytest.mark.parametrize("stage", ["extract", "query"])
def test_local_context_selects_complete_originals_within_budget(
    db: sessionmaker[Session], stage: str
) -> None:
    bodies = [f"Aurora registro {index}: " + "dado " * 600 for index in range(3)]
    export = "\n".join(
        f"05/11/2026, 10:0{index} - Pessoa: {body}" for index, body in enumerate(bodies)
    )
    with db.begin() as session:
        job = commit_import(
            session, parse_export(export.encode(), "DMY", "UTC"), "budget.txt", "Fixture", None
        )
        conversation = job.conversation_id
    settings = local_settings()
    provider = CaptureProvider()
    with db.begin() as session:
        originals = list(
            session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sequence)
            )
        )
        if stage == "extract":
            asyncio.run(
                process_batch(
                    session, [originals[-1]], settings, provider, MockEmbeddingProvider(256)
                )
            )
            assert originals[-1].id in {row.id for row in provider.inputs}
        else:
            asyncio.run(
                answer_query(
                    session,
                    settings,
                    provider,
                    MockEmbeddingProvider(256),
                    Query(question="Aurora", conversation_id=conversation),
                )
            )
        assert len(provider.inputs) == 2
        assert (
            sum(len(row.content) for row in provider.inputs) <= settings.local_ai_context_characters
        )
        assert sum(len(row.content) for row in originals) > settings.local_ai_context_characters
        original_text = {row.id: row.content for row in originals}
        assert all(row.content == original_text[row.id] for row in provider.inputs)


def test_compact_input_preserves_distinct_speakers_and_conversations() -> None:
    conversation = UUID("10000000-0000-4000-8000-000000000001")
    other = UUID("10000000-0000-4000-8000-000000000002")
    speaker = UUID("20000000-0000-4000-8000-000000000001")
    second_speaker = UUID("20000000-0000-4000-8000-000000000002")
    inputs = [
        ExtractionInput.model_validate(
            {
                "id": f"30000000-0000-4000-8000-{index:012d}",
                "content": "Texto integral " * 100,
                "sent_at": "2026-11-05T10:00:00Z",
                "sender_name_snapshot": "Alex",
                "sender_contact_id": author,
                "conversation_id": room,
            }
        )
        for index, (room, author) in enumerate(
            [
                (conversation, speaker),
                (conversation, second_speaker),
                (other, speaker),
                (conversation, speaker),
            ],
            1,
        )
    ]
    inputs[0] = inputs[0].model_copy(
        update={"external_message_id": "same-external-id", "message_type": "text"}
    )
    inputs[2] = inputs[2].model_copy(update={"external_message_id": "same-external-id"})
    from app.integrations.local import LocalConnectorMetadata

    inputs[1] = inputs[1].model_copy(
        update={"connector_metadata": LocalConnectorMetadata(quoted_message_id="same-external-id")}
    )
    inputs[3] = inputs[3].model_copy(
        update={"connector_metadata": LocalConnectorMetadata(quoted_message_id="same-external-id")}
    )
    wire = compact_inputs(inputs, aliases=True)
    assert wire[0]["message_type"] == "text"
    assert wire[1]["quoted_message_ref"] == "m1" and wire[3]["quoted_message_ref"] == "m1"
    unresolved = compact_inputs([inputs[1]], aliases=True)[0]
    assert (
        unresolved["quoted_message_unresolved"] is True and "quoted_message_ref" not in unresolved
    )
    assert [row["id"] for row in wire] == ["m1", "m2", "m3", "m4"]
    assert wire[0]["conversation_ref"] == wire[1]["conversation_ref"] == wire[3]["conversation_ref"]
    assert wire[0]["conversation_ref"] != wire[2]["conversation_ref"]
    assert wire[0]["sender_ref"] != wire[1]["sender_ref"]
    assert wire[0]["sender_ref"] == wire[3]["sender_ref"]
    assert all(row["content"] == item.content for row, item in zip(wire, inputs, strict=True))
    assert str(conversation) not in json.dumps(wire) and str(speaker) not in json.dumps(wire)
    assert processing_identity(local_settings()) != processing_identity(
        local_settings().model_copy(update={"local_ai_context_characters": 16000})
    )
