from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.ingestion.parser import parse_export
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider
from app.main import create_app
from app.models.entities import ImportJob, Memory, Message, MessageEmbedding, MessageWorkItem
from app.services.imports import commit_import
from app.services.knowledge import conversation_detail
from app.services.reprocessing import enqueue_reprocessing
from app.workers.runner import run_once

pytestmark = pytest.mark.database
TEXT = "03/11/2026, 10:00 - Alice: Eu trabalho como engenheira.\n03/11/2026, 10:00 - Bob: Eu moro em Recife."


def ingest(db: sessionmaker[Session], text: str = TEXT) -> UUID:
    with db.begin() as session:
        return commit_import(
            session,
            parse_export(text.encode(), "DMY", "UTC"),
            "synthetic.txt",
            "Synthetic reprocess",
            None,
        ).conversation_id


def test_concurrent_reprocessing_reuses_job_and_keeps_originals_and_memories_unique(
    db: sessionmaker[Session],
) -> None:
    conversation = ingest(db)

    def schedule(_: int) -> UUID:
        with db.begin() as session:
            return enqueue_reprocessing(session, conversation, get_settings()).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(schedule, range(2)))
    assert ids[0] == ids[1]
    for _ in range(8):
        if not run_once(db, get_settings(), MockLLMProvider(), MockEmbeddingProvider(256)):
            break
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 2
        assert session.scalar(select(func.count()).select_from(Memory)) == 2
        assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 2
        assert session.scalar(select(func.count()).select_from(MessageWorkItem)) == 2
        job = session.get(ImportJob, ids[0])
        assert job and job.status == "completed" and job.processed_messages == 2


def test_reprocessing_refuses_to_run_under_an_unexpected_model_configuration(
    db: sessionmaker[Session],
) -> None:
    conversation = ingest(db)
    run_once(db, get_settings(), MockLLMProvider(), MockEmbeddingProvider(256))
    with db.begin() as session:
        job_id = enqueue_reprocessing(session, conversation, get_settings()).id
    changed = Settings(
        _env_file=None,
        llm_provider="ollama",
        local_ai_enabled=True,
        llm_model="synthetic:local",
        ollama_llm_digest="sha256:" + "a" * 64,
    )
    assert run_once(db, changed, MockLLMProvider(), MockEmbeddingProvider(256))
    with db() as session:
        job = session.get(ImportJob, job_id)
        assert job and job.status == "failed" and job.processed_messages == 0


def test_reprocessing_character_boundary_does_not_skip_work_after_a_stale_revision(
    db: sessionmaker[Session],
) -> None:
    text = "\n".join(
        f"03/11/2026, 10:0{index} - Alice: {index} " + "documento " * 790
        for index in range(6)
    )
    conversation = ingest(db, text)
    settings = get_settings()
    for _ in range(3):
        run_once(db, settings, MockLLMProvider(), MockEmbeddingProvider(256))
    with db.begin() as session:
        job_id = enqueue_reprocessing(session, conversation, settings).id
        row = session.scalar(select(Message).where(Message.conversation_id == conversation))
        assert row is not None
        row.revision += 1
        session.execute(delete(MessageEmbedding))
    assert run_once(db, settings, MockLLMProvider(), MockEmbeddingProvider(256))
    with db() as session:
        job = session.get(ImportJob, job_id)
        assert job and job.status == "processing" and 0 < job.processed_messages < 6
    assert run_once(db, settings, MockLLMProvider(), MockEmbeddingProvider(256))
    with db() as session:
        job = session.get(ImportJob, job_id)
        assert job and job.status == "completed" and job.processed_messages == 6
        assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 5


def test_message_cursor_is_stable_and_cannot_cross_conversation_boundaries(
    db: sessionmaker[Session],
) -> None:
    conversation = ingest(db)
    other = ingest(db, TEXT.replace("Recife", "Lisboa"))
    with db() as session:
        first = conversation_detail(session, conversation, limit=1, order="desc")
        foreign = conversation_detail(session, other, limit=1).messages[0].id
        cursor = first.messages[0].id
    with db.begin() as session:
        commit_import(
            session,
            parse_export(b"03/11/2026, 11:00 - Alice: New synthetic message.", "DMY", "UTC"),
            "new.txt",
            "Synthetic",
            conversation,
        )
    with TestClient(create_app()) as client:
        headers = {"Authorization": "Bearer " + get_settings().auth_secret.get_secret_value()}
        page = client.get(
            f"/api/v1/conversations/{conversation}?order=desc&before_id={cursor}", headers=headers
        )
        assert page.status_code == 200 and len(page.json()["messages"]) == 1
        assert page.json()["messages"][0]["id"] != str(cursor)
        assert (
            client.get(
                f"/api/v1/conversations/{conversation}?before_id={foreign}", headers=headers
            ).status_code
            == 404
        )


def test_source_validation_rejects_old_revisions_and_deleted_sources(
    db: sessionmaker[Session],
) -> None:
    conversation = ingest(db)
    with db() as session:
        source = session.scalar(select(Message).where(Message.conversation_id == conversation))
        assert source
        source_id = str(source.id)
    with TestClient(create_app()) as client:
        headers = {"Authorization": "Bearer " + get_settings().auth_secret.get_secret_value()}
        references = [{"id": source_id, "revision": 1}, {"id": source_id, "revision": 2}]
        result = client.post(
            "/api/v1/messages/validate", headers=headers, json={"sources": references}
        )
        assert result.status_code == 200 and result.json() == references[:1]
        with db.begin() as session:
            row = session.get(Message, UUID(source_id))
            assert row
            row.content = "Synthetic edited content"
            row.revision = 2
        assert (
            client.post(
                "/api/v1/messages/validate", headers=headers, json={"sources": references}
            ).json()
            == references[1:]
        )
        with db.begin() as session:
            row = session.get(Message, UUID(source_id))
            assert row
            row.delivery_state = "revoked"
            row.content = ""
        assert (
            client.post(
                "/api/v1/messages/validate", headers=headers, json={"sources": references}
            ).json()
            == []
        )
