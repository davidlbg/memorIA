import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core.ingestion.parser import parse_export
from app.core.memory.pipeline import validate_memory
from app.core.retrieval.search import rank_fusion, search
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider, ProviderError
from app.models.entities import (
    Contact,
    Conversation,
    ImportJob,
    Memory,
    MemoryEmbedding,
    MemorySource,
    Message,
    MessageEmbedding,
)
from app.schemas.domain import ExtractedMemory, Query
from app.services.imports import commit_import
from app.services.knowledge import answer_query
from app.workers.runner import run_once

pytestmark = pytest.mark.database
EXPORT = "31/10/2026, 10:00 - João: Eu trabalho como engenheiro.\n31/10/2026, 10:01 - João: Pretendo mudar de emprego.\n31/10/2026, 10:02 - Alice: Eu prometo enviar o documento sexta.\n31/10/2026, 10:03 - João: Desisti de mudar de emprego."


def ingest(factory: sessionmaker[Session], content: str = EXPORT) -> tuple:
    with factory.begin() as session:
        job = commit_import(
            session,
            parse_export(content.encode(), "DMY", "UTC"),
            "synthetic.txt",
            "Synthetic",
            None,
        )
        return job.id, job.conversation_id


def process(factory: sessionmaker[Session]) -> None:
    assert run_once(factory, get_settings(), MockLLMProvider(), MockEmbeddingProvider(256))


def test_concurrent_imports_idempotent_and_persisted(db: sessionmaker[Session]) -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: ingest(db), range(2)))
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 4
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1
        assert session.scalar(select(func.sum(ImportJob.imported_messages))) == 4
        assert results[0][1] == results[1][1]


def test_memory_sources_consolidation_and_deferred_invariant(db: sessionmaker[Session]) -> None:
    _, conversation_id = ingest(db)
    process(db)
    with db() as session:
        memories = list(session.scalars(select(Memory).order_by(Memory.observed_at)))
        assert len(memories) == 4
        assert memories[1].status == "outdated"
        assert memories[3].status == "active"
        assert session.scalar(select(func.count()).select_from(MemorySource)) == 4
        assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 4
    with pytest.raises(IntegrityError), db.begin() as session:
        session.add(
            Memory(
                conversation_id=conversation_id,
                memory_type="fact",
                content="Unsupported",
                normalized_content="unsupported",
                fingerprint="f" * 64,
                confidence=1,
                status="active",
                observed_at=datetime.now(UTC),
                evidence_summary="no source",
            )
        )
    with pytest.raises(IntegrityError), db.begin() as session:
        session.execute(text("DELETE FROM memory_sources"))


def test_invalid_extracted_sources_rejected(db: sessionmaker[Session]) -> None:
    ingest(db)
    with db() as session:
        message = session.scalar(select(Message).limit(1))
        assert message is not None
        item = ExtractedMemory(
            memory_type="fact",
            subject=message.sender_name_snapshot,
            content=message.content,
            confidence=0.9,
            observed_at=message.sent_at,
            source_message_ids=[uuid4()],
            evidence_summary="test",
            evidence_quote=message.content,
        )
        with pytest.raises(ProviderError):
            validate_memory(item, {message.id: message})
        item.source_message_ids = [message.id]
        item.evidence_quote = "fabricated quote"
        with pytest.raises(ProviderError):
            validate_memory(item, {message.id: message})


class FailingLLM(MockLLMProvider):
    async def extract_memories(self, messages):  # type: ignore[no-untyped-def]
        raise ProviderError("synthetic failure")


def test_worker_failure_rolls_back_ai_but_keeps_messages(db: sessionmaker[Session]) -> None:
    job_id, _ = ingest(db)
    for _ in range(3):
        assert run_once(db, get_settings(), FailingLLM(), MockEmbeddingProvider(256))
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 4
        assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 0
        job = session.scalar(select(ImportJob))
        assert job and job.status == "failed" and job.attempts == 3
    from app.main import create_app

    headers = {"Authorization": f"Bearer {get_settings().auth_secret.get_secret_value()}"}
    with TestClient(create_app()) as client:
        resumed = client.post(f"/api/v1/imports/{job_id}/retry", headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "pending"
        assert resumed.json()["attempts"] == 0
    process(db)
    with db() as session:
        job = session.get(ImportJob, job_id)
        assert job and job.status == "completed" and job.processed_messages == 4
        assert session.scalar(select(func.count()).select_from(MemorySource)) == 4


def test_two_workers_never_process_same_job_twice(db: sessionmaker[Session]) -> None:
    ingest(db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda _: run_once(
                    db, get_settings(), MockLLMProvider(), MockEmbeddingProvider(256)
                ),
                range(2),
            )
        )
    assert outcomes.count(True) == 1
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Memory)) == 4


async def test_hybrid_search_filters_models_and_evidence(db: sessionmaker[Session]) -> None:
    _, conversation_id = ingest(db)
    await asyncio.to_thread(process, db)
    settings = get_settings()
    with db() as session:
        alice = session.scalar(select(Contact).where(Contact.display_name == "Alice"))
        assert alice
        ids = await search(
            session,
            settings,
            MockEmbeddingProvider(256),
            kind="messages",
            query="documento",
            contact_id=alice.id,
            conversation_id=conversation_id,
            limit=5,
        )
        assert len(ids) == 1
        assert session.get(Message, ids[0]).content.endswith("documento sexta.")
        assert (
            await search(
                session,
                settings,
                MockEmbeddingProvider(256),
                kind="messages",
                query="documento",
                date_from=datetime(2027, 1, 1, tzinfo=UTC),
            )
            == []
        )
        response = await answer_query(
            session,
            settings,
            MockLLMProvider(),
            MockEmbeddingProvider(256),
            Query(question="documento", contact_id=alice.id),
        )
        assert response.sources and not response.insufficient_evidence
        assert all(c.quote in response.sources[0].content for c in response.claims)
        missing = await answer_query(
            session,
            settings,
            MockLLMProvider(),
            MockEmbeddingProvider(256),
            Query(question="xylophonzq"),
        )
        assert missing.insufficient_evidence
        # Incompatible models never participate in semantic ranking.
        for embedding in session.scalars(select(MessageEmbedding)):
            embedding.model_name = "incompatible:model:256"
        session.flush()
        ids = await search(
            session,
            settings,
            MockEmbeddingProvider(256),
            kind="messages",
            query="documento",
            limit=5,
        )
        assert len(ids) == 1  # keyword search still works


def test_api_preview_auth_pagination_deletion_retry(db: sessionmaker[Session]) -> None:
    from app.main import create_app

    headers = {"Authorization": f"Bearer {get_settings().auth_secret.get_secret_value()}"}
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/conversations").status_code == 401
        assert client.post("/mcp/", json={}).status_code == 401
        preview = client.post(
            "/api/v1/imports/preview",
            headers=headers,
            files={"file": ("synthetic.txt", EXPORT.encode(), "text/plain")},
            data={"date_order": "DMY", "timezone": "UTC"},
        )
        assert preview.status_code == 200, preview.text
        with db() as session:
            assert session.scalar(select(func.count()).select_from(Message)) == 0
        data = {
            "date_order": "DMY",
            "timezone": "UTC",
            "title": "Synthetic",
            "preview_hash": preview.json()["file_hash"],
        }
        imported = client.post(
            "/api/v1/imports",
            headers=headers,
            files={"file": ("synthetic.txt", EXPORT.encode(), "text/plain")},
            data=data,
        )
        assert imported.status_code == 201, imported.text
        twice = client.post(
            "/api/v1/imports",
            headers=headers,
            files={"file": ("synthetic.txt", EXPORT.encode(), "text/plain")},
            data=data,
        )
        assert twice.json()["imported_messages"] == 0
        process(db)
        assert len(client.get("/api/v1/messages/search?limit=1", headers=headers).json()) == 1
        assert client.get("/api/v1/messages/search?limit=1000", headers=headers).status_code == 422
        memory = client.get("/api/v1/memories", headers=headers).json()[0]
        assert client.delete(f"/api/v1/memories/{memory['id']}", headers=headers).status_code == 200
        assert client.get(f"/api/v1/memories/{memory['id']}", headers=headers).status_code == 404
        conversation_id = imported.json()["conversation_id"]
        assert (
            client.delete(f"/api/v1/conversations/{conversation_id}", headers=headers).status_code
            == 200
        )
        with db() as session:
            for entity in (
                Conversation,
                Contact,
                ImportJob,
                Message,
                Memory,
                MemorySource,
                MemoryEmbedding,
                MessageEmbedding,
            ):
                assert session.scalar(select(func.count()).select_from(entity)) == 0


def test_body_limit_and_safe_validation_errors(db: sessionmaker[Session]) -> None:
    from app.main import create_app

    with TestClient(create_app()) as client:
        headers = {"Authorization": f"Bearer {get_settings().auth_secret.get_secret_value()}"}
        response = client.post("/api/v1/imports/preview", headers=headers, content=b"x" * 5_100_000)
        assert response.status_code == 413
        response = client.post(
            "/api/v1/assistant/query",
            headers=headers,
            json={"question": "", "sensitive": "never echo me"},
        )
        assert response.status_code == 422
        assert "never echo me" not in response.text


async def test_mcp_tools_reuse_services_and_validate_limits(db: sessionmaker[Session]) -> None:
    from app.mcp.server import mcp

    _, conversation_id = ingest(db)
    await asyncio.to_thread(process, db)
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {
        "search_messages",
        "search_memories",
        "get_conversation",
        "get_contact_context",
        "get_timeline",
    }
    assert all(tool.annotations and tool.annotations.readOnlyHint for tool in tools)
    result = await mcp.call_tool(
        "get_conversation",
        {"conversation_id": str(conversation_id), "include_messages": True, "limit": 2},
    )
    assert result
    messages = await mcp.call_tool("search_messages", {"query": "documento", "limit": 2})
    assert messages


def test_ranking_rewards_independent_signals() -> None:
    first, second, third = uuid4(), uuid4(), uuid4()
    assert rank_fusion([[first, second], [third, second]], 3)[0] == second


def test_concurrent_source_deletion_keeps_one_supported_memory(db: sessionmaker[Session]) -> None:
    ingest(
        db,
        "31/10/2026, 10:00 - Alice: I work as an engineer.\n31/10/2026, 10:01 - Alice: I work as an engineer.",
    )
    process(db)
    with db() as session:
        source_ids = list(session.scalars(select(MemorySource.id)))
        assert len(source_ids) == 2

    def remove(source_id) -> bool:  # type: ignore[no-untyped-def]
        try:
            with db.begin() as session:
                session.execute(text("DELETE FROM memory_sources WHERE id=:id"), {"id": source_id})
            return True
        except IntegrityError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(remove, source_ids))
    assert sorted(outcomes) == [False, True]
    with db() as session:
        assert session.scalar(select(func.count()).select_from(MemorySource)) == 1


def test_pipeline_restarts_at_committed_batch_boundary(db: sessionmaker[Session]) -> None:
    export = "\n".join(
        f"31/10/2026, 10:{i:02d} - Alice: I work as engineer {i}." for i in range(45)
    )
    job_id, _ = ingest(db, export)
    process(db)
    with db() as session:
        job = session.get(ImportJob, job_id)
        assert job and job.processed_messages == 20 and job.status == "processing"
    process(db)
    process(db)
    with db() as session:
        job = session.get(ImportJob, job_id)
        assert job and job.processed_messages == 45 and job.status == "completed"
        assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 45


def test_late_history_and_repeated_intentions_preserve_episodes(db: sessionmaker[Session]) -> None:
    newer = "31/10/2026, 10:03 - João: Desisti de mudar de emprego."
    _, conversation_id = ingest(db, newer)
    process(db)
    with db.begin() as session:
        commit_import(
            session,
            parse_export(
                "31/10/2026, 10:01 - João: Pretendo mudar de emprego.".encode(), "DMY", "UTC"
            ),
            "older.txt",
            "Synthetic",
            conversation_id,
        )
    process(db)
    with db.begin() as session:
        commit_import(
            session,
            parse_export(
                "31/10/2026, 10:05 - João: Pretendo mudar de emprego.".encode(), "DMY", "UTC"
            ),
            "later.txt",
            "Synthetic",
            conversation_id,
        )
    process(db)
    with db() as session:
        memories = list(session.scalars(select(Memory).order_by(Memory.observed_at)))
        assert len(memories) == 3
        assert [m.status for m in memories] == ["outdated", "active", "active"]


def test_provider_selection_requires_configured_consent_and_persists(
    db: sessionmaker[Session],
) -> None:
    from app.main import create_app
    from app.services.settings import runtime_settings

    headers = {"Authorization": f"Bearer {get_settings().auth_secret.get_secret_value()}"}
    with TestClient(create_app()) as client:
        assert (
            client.patch(
                "/api/v1/settings/provider", headers=headers, json={"llm_provider": "openai"}
            ).status_code
            == 422
        )
        assert (
            client.patch(
                "/api/v1/settings/provider", headers=headers, json={"llm_provider": "mock"}
            ).status_code
            == 200
        )
        assert (
            client.patch(
                "/api/v1/settings/provider", headers=headers, json={"llm_provider": "unknown"}
            ).status_code
            == 422
        )
    with db() as session:
        assert runtime_settings(session).llm_provider == "mock"
