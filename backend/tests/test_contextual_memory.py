import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.ingestion.parser import parse_export
from app.core.memory.pipeline import process_batch, validate_memory
from app.core.retrieval.context import dialogue_context
from app.integrations.local import OllamaProvider
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider, ProviderError
from app.models.entities import Memory, MemoryContext, MemoryEmbedding, MemorySource, Message
from app.schemas.domain import (
    Claim,
    ExtractedMemory,
    Extraction,
    GeneratedAnswer,
    Query,
    SourceMessage,
    SupportingQuote,
)
from app.services.imports import commit_import
from app.services.knowledge import answer_query, delete_memory, memory_view
from app.services.whatsapp import invalidate_sources

pytestmark = pytest.mark.database
EXPORT = "03/11/2026, 10:00 - Alice: Vamos revisar o projeto Aurora?\n03/11/2026, 10:01 - Bob: Combinado, sexta às 14h."


def ingest(db: sessionmaker[Session]) -> UUID:
    with db.begin() as session:
        job = commit_import(
            session,
            parse_export(EXPORT.encode(), "DMY", "UTC"),
            "synthetic-context.txt",
            "Projeto Aurora",
            None,
        )
        return job.conversation_id


class ContextMemoryProvider(MockLLMProvider):
    def __init__(self, paraphrase: str = "Revisão do projeto Aurora combinada para sexta às 14h."):
        self.paraphrase = paraphrase

    async def extract_memories(self, messages: list[SourceMessage]) -> Extraction:
        assert len(messages) == 2
        return Extraction(
            memories=[
                ExtractedMemory(
                    memory_type="event",
                    subject=None,
                    content=self.paraphrase,
                    confidence=0.8,
                    observed_at=max(m.sent_at for m in messages),
                    source_message_ids=[m.id for m in messages],
                    evidence_summary="Interpretação do diálogo com resposta de confirmação.",
                    evidence_quote=messages[0].content,
                    evidence_basis="interpretation",
                    supporting_quotes=[
                        SupportingQuote(message_id=m.id, quote=m.content) for m in messages
                    ],
                )
            ]
        )


def local_settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="ollama",
        local_ai_enabled=True,
        llm_model="synthetic:local",
        ollama_llm_digest="sha256:" + "a" * 64,
    )


def test_local_json_support_and_canonical_date_persist_without_relaxing_author_guard(
    db: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = ingest(db)
    settings = local_settings()

    invalid_subject = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [{"name": settings.llm_model, "digest": settings.ollama_llm_digest}]
                },
            )
        payload = json.loads(request.content)
        inputs = json.loads(payload["messages"][1]["content"])
        assert isinstance(payload["format"], dict)
        assert '"subject":null' in payload["messages"][0]["content"]
        assert '"observed_at"' not in payload["messages"][0]["content"]
        memory = {
            "memory_type": "commitment",
            "subject": "Bob" if invalid_subject else None,
            "content": "Bob aceitou revisar o projeto Aurora na sexta às 14h.",
            "confidence": 0.8,
            "evidence_basis": "interpretation",
            "current_quote": {"message_id": inputs[-1]["id"], "quote": inputs[-1]["content"]},
            "supporting_quotes": [{"message_id": item["id"], "quote": item["content"]} for item in inputs[:-1]],
        }
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"content": json.dumps({"memories": [memory]})},
            },
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "app.integrations.local.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )
    with db.begin() as session:
        messages = list(
            session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sent_at)
            )
        )
        provider = OllamaProvider(settings)
        asyncio.run(
            process_batch(session, [messages[1]], settings, provider, MockEmbeddingProvider(256))
        )
        memory = session.scalar(select(Memory))
        assert (
            memory
            and memory.subject_contact_id is None
            and memory.observed_at == messages[1].sent_at
        )
        view = memory_view(session, memory)
        assert len(view.supporting_quotes) == 2 and len(view.sources) == 2
        assert view.evidence_summary == "Fontes originais citadas: 2. Consulte os trechos de apoio."
        extracted = asyncio.run(
            provider.extract_memories([SourceMessage.model_validate(item) for item in messages])
        ).memories[0]
        with pytest.raises(ProviderError, match="Unsupported subject"):
            validate_memory(
                extracted.model_copy(update={"subject": "Bob"}),
                {item.id: item for item in messages},
            )
        invalid_subject = True
        with pytest.raises(ProviderError, match="Unsupported subject"):
            asyncio.run(
                provider.extract_memories([SourceMessage.model_validate(item) for item in messages])
            )


class ReplyCitationProvider(ContextMemoryProvider):
    async def extract_memories(self, messages: list[SourceMessage]) -> Extraction:
        reply = messages[-1]
        return Extraction(
            memories=[
                ExtractedMemory(
                    memory_type="commitment",
                    subject=reply.sender_name_snapshot,
                    content="Revisão do projeto Aurora combinada para sexta às 14h.",
                    confidence=0.8,
                    observed_at=reply.sent_at,
                    source_message_ids=[reply.id],
                    evidence_basis="interpretation",
                    evidence_summary="Interpretação da resposta com contexto anterior.",
                    evidence_quote=reply.content,
                    supporting_quotes=[SupportingQuote(message_id=reply.id, quote=reply.content)],
                )
            ]
        )


def test_same_display_name_does_not_assign_memory_to_the_first_distinct_contact(
    db: sessionmaker[Session],
) -> None:
    conversation = ingest(db)

    class SameNameProvider(ContextMemoryProvider):
        async def extract_memories(self, messages: list[SourceMessage]) -> Extraction:
            result = await super().extract_memories(messages)
            result.memories[0].subject = "Mesmo nome"
            return result

    with db.begin() as session:
        messages = list(
            session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sent_at)
            )
        )
        assert messages[0].sender_contact_id != messages[1].sender_contact_id
        for message in messages:
            message.sender_name_snapshot = "Mesmo nome"
        session.flush()
        asyncio.run(
            process_batch(
                session,
                [messages[1]],
                local_settings(),
                SameNameProvider(),
                MockEmbeddingProvider(256),
            )
        )
        memory = session.scalar(select(Memory))
        assert memory and memory.subject_contact_id is None
        assert len(memory_view(session, memory).sources) == 2


def test_uncited_input_is_traceable_and_its_edit_purges_the_derived_memory(
    db: sessionmaker[Session],
) -> None:
    conversation = ingest(db)
    with db.begin() as session:
        messages = list(
            session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sent_at)
            )
        )
        asyncio.run(
            process_batch(
                session,
                [messages[1]],
                local_settings(),
                ReplyCitationProvider(),
                MockEmbeddingProvider(256),
            )
        )
        memory = session.scalar(select(Memory))
        assert memory
        view = memory_view(session, memory)
        assert [item.id for item in view.sources] == [messages[1].id]
        assert [item.id for item in view.context_sources] == [messages[0].id]
        assert view.context_sources[0].content == messages[0].content
        # This was not a model citation; it is a separately tracked input.
        assert len(view.supporting_quotes) == 1
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.execute(
                    delete(MemoryContext).where(
                        MemoryContext.memory_id == memory.id,
                        MemoryContext.message_id == messages[0].id,
                    )
                )
                session.add(
                    MemoryContext(memory_id=memory.id, message_id=messages[0].id, revision=999)
                )
                session.flush()
        invalidate_sources(session, messages[0])
        messages[0].content = "Pergunta alterada."
        messages[0].revision += 1
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Memory)) == 0
        assert session.scalar(select(func.count()).select_from(MemoryContext)) == 0
        assert session.scalar(select(func.count()).select_from(MemoryEmbedding)) == 0


def test_contextual_memory_has_all_quotes_is_idempotent_and_invalidates_on_support_edit(
    db: sessionmaker[Session],
) -> None:
    conversation = ingest(db)
    with db.begin() as session:
        messages = list(
            session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sent_at)
            )
        )
        # The current reply can recover its preceding question even when jobs
        # arrive one message at a time, as in the native connector.
        asyncio.run(
            process_batch(
                session,
                [messages[1]],
                local_settings(),
                ContextMemoryProvider(),
                MockEmbeddingProvider(256),
            )
        )
    with db.begin() as session:
        memory = session.scalar(select(Memory))
        assert memory
        assert memory.evidence_basis == "interpretation"
        view = memory_view(session, memory)
        assert len(view.supporting_quotes) == 2 and len(view.sources) == 2
        messages = list(
            session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sent_at)
            )
        )
        asyncio.run(
            process_batch(
                session,
                [messages[1]],
                local_settings(),
                ContextMemoryProvider("O encontro do Aurora será sexta às 14h."),
                MockEmbeddingProvider(256),
            )
        )
        assert session.scalar(select(func.count()).select_from(Memory)) == 1
        invalidate_sources(session, messages[0])
        messages[0].content = "Pergunta editada."
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Memory)) == 0
        assert session.scalar(select(func.count()).select_from(MemorySource)) == 0
        assert session.scalar(select(func.count()).select_from(MemoryEmbedding)) == 0


def test_deleted_memory_retains_only_support_identifiers(db: sessionmaker[Session]) -> None:
    conversation = ingest(db)
    with db.begin() as session:
        messages = list(
            session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sent_at)
            )
        )
        asyncio.run(
            process_batch(
                session,
                messages,
                local_settings(),
                ContextMemoryProvider(),
                MockEmbeddingProvider(256),
            )
        )
        memory = session.scalar(select(Memory))
        assert memory
        delete_memory(session, memory.id)
        assert all(source.evidence_quote == "" for source in session.scalars(select(MemorySource)))


def test_dialogue_expansion_respects_filters_and_assistant_receives_the_reply(
    db: sessionmaker[Session],
) -> None:
    conversation = ingest(db)

    class ContextAnswer(MockLLMProvider):
        async def generate(self, prompt: str, context: list[SourceMessage]) -> GeneratedAnswer:
            assert len(context) == 2
            assert any(m.retrieval_role == "context" and "14h" in m.content for m in context)
            assert all(m.conversation_title == "Projeto Aurora" for m in context)
            return GeneratedAnswer(
                claims=[
                    Claim(
                        kind="interpretation",
                        text="A revisão foi combinada para sexta às 14h.",
                        quote=context[1].content,
                        source_message_ids=[m.id for m in context],
                    )
                ]
            )

    with db.begin() as session:
        messages = list(
            session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sent_at)
            )
        )
        assert (
            len(
                dialogue_context(
                    session, [messages[0]], date_to=datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
                )
            )
            == 1
        )
        assert (
            len(dialogue_context(session, [messages[0]], contact_id=messages[0].sender_contact_id))
            == 1
        )
        answer = asyncio.run(
            answer_query(
                session,
                get_settings(),
                ContextAnswer(),
                MockEmbeddingProvider(256),
                Query(question="Aurora", conversation_id=conversation),
            )
        )
        assert len(answer.sources) == 2 and answer.claims[0].kind == "interpretation"


def test_interpretation_rejects_missing_or_fabricated_support(db: sessionmaker[Session]) -> None:
    conversation = ingest(db)
    with db() as session:
        messages = list(
            session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation)
                .order_by(Message.sent_at)
            )
        )
        extracted = asyncio.run(
            ContextMemoryProvider().extract_memories(
                [SourceMessage.model_validate(m) for m in messages]
            )
        )
        item = extracted.memories[0]
        item.supporting_quotes[1].quote = "Invented quote"
        with pytest.raises(ProviderError, match="support"):
            validate_memory(item, {m.id: m for m in messages})
