from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import v1
from app.config import get_settings
from app.core.ingestion.parser import Preview, parse_export
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider
from app.main import create_app
from app.models.entities import Conversation, ImportJob
from app.schemas.domain import Extraction, SourceMessage
from app.services.imports import commit_import
from app.workers.runner import run_once

pytestmark = pytest.mark.database


def test_worker_publishes_processing_while_ai_is_running_and_remains_exclusive(
    db: sessionmaker[Session],
) -> None:
    with db.begin() as session:
        job_id = commit_import(
            session,
            parse_export(b"03/11/2026, 10:00 - Alice: Eu moro em Recife.", "DMY", "UTC"),
            "fixture.txt",
            "Synthetic",
            None,
        ).id
    entered, release = Event(), Event()

    class WaitingLLM(MockLLMProvider):
        async def extract_memories(self, messages: list[SourceMessage]) -> Extraction:
            entered.set()
            if not release.wait(timeout=10):
                raise RuntimeError("Synthetic model was not released")
            return await super().extract_memories(messages)

    with ThreadPoolExecutor(max_workers=2) as executor:
        working = executor.submit(
            run_once, db, get_settings(), WaitingLLM(), MockEmbeddingProvider(256)
        )
        try:
            assert entered.wait(timeout=5)
            with db() as session:
                job = session.get(ImportJob, job_id)
                assert job and job.status == "processing" and job.processed_messages == 0
            assert not run_once(db, get_settings(), MockLLMProvider(), MockEmbeddingProvider(256))
            with db.begin() as session:
                second_id = commit_import(
                    session,
                    parse_export(b"03/11/2026, 10:01 - Bob: Eu moro em Natal.", "DMY", "UTC"),
                    "second-fixture.txt",
                    "Other synthetic conversation",
                    None,
                ).id
            assert run_once(db, get_settings(), MockLLMProvider(), MockEmbeddingProvider(256))
            with db() as session:
                second = session.get(ImportJob, second_id)
                assert second and second.status == "completed"
        finally:
            release.set()
        assert working.result(timeout=5)
    with db() as session:
        job = session.get(ImportJob, job_id)
        assert job and job.status == "completed" and job.processed_messages == 1


def test_import_waiting_for_worker_lock_does_not_block_health_or_connector_control(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    text = b"03/11/2026, 10:00 - Alice: Synthetic initial message."
    preview = parse_export(text, "DMY", "UTC")
    with db.begin() as session:
        conversation = commit_import(
            session, preview, "fixture.txt", "Synthetic", None
        ).conversation_id
    entered = Event()

    def waiting_import(
        session: Session, preview: Preview, filename: str, title: str, target: UUID | None
    ) -> ImportJob:
        entered.set()
        return commit_import(session, preview, filename, title, target)

    monkeypatch.setattr(v1, "commit_import", waiting_import)
    headers = {"Authorization": "Bearer " + get_settings().auth_secret.get_secret_value()}
    with TestClient(create_app()) as client, ThreadPoolExecutor(max_workers=2) as executor:
        with db.begin() as locked:
            locked.scalar(
                select(Conversation).where(Conversation.id == conversation).with_for_update()
            )
            waiting = executor.submit(
                client.post,
                "/api/v1/imports",
                headers=headers,
                files={"file": ("fixture.txt", text, "text/plain")},
                data={
                    "title": "Synthetic",
                    "date_order": "DMY",
                    "timezone": "UTC",
                    "preview_hash": preview.file_hash,
                    "conversation_id": str(conversation),
                },
            )
            assert entered.wait(timeout=5)
            try:
                healthy = executor.submit(client.get, "/api/v1/health", headers=headers).result(
                    timeout=5
                )
                assert healthy.status_code == 200
                assert not waiting.done()
            finally:
                # Releasing the actual database row lock also releases a failed
                # old implementation without leaving a test request hanging.
                locked.rollback()
        assert waiting.result(timeout=5).status_code == 201
