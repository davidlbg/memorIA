import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core.ingestion.parser import parse_export
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider, ProviderError
from app.main import create_app
from app.models.entities import AIConfiguration, ImportJob, Message, MessageWorkItem
from app.services.imports import commit_import
from app.services.settings import runtime_settings
from app.workers.indexer import index_once
from app.workers.runner import run_once

pytestmark = pytest.mark.database
KEY = "sk-synthetic-credential-used-only-in-tests"
HEADERS = {"Authorization": "Bearer test-only-token-000000000000000000000000000"}


def test_authenticated_key_lifecycle_encrypted_persistent_and_fail_closed(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    key_file = tmp_path / "ai.key"
    key_file.write_bytes(b"k" * 32)
    monkeypatch.setenv("AI_CREDENTIALS_KEY_FILE", str(key_file))
    get_settings.cache_clear()
    calls: list[str] = []
    reject = False

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if reject:
            return httpx.Response(401, json={"error": {"message": KEY}})
        payload = json.loads(request.content)
        if request.url.path.endswith("embeddings"):
            assert payload["input"] == ["Teste de conexão do WhatsApp Memory."]
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 256}]})
        assert json.loads(payload["messages"][1]["content"])["evidence"] == []
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"claims":[]}'}}]})

    client_type = httpx.AsyncClient
    monkeypatch.setattr("app.integrations.providers.httpx.AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    try:
        with TestClient(create_app()) as client:
            assert client.post("/api/v1/settings/openai", json={"api_key": KEY, "consent": True}).status_code == 401
            invalid = client.post("/api/v1/settings/openai", headers=HEADERS, json={"api_key": KEY})
            assert invalid.status_code == 422 and KEY not in invalid.text and not calls
            configured = client.post("/api/v1/settings/openai", headers=HEADERS, json={"api_key": KEY, "consent": True})
            assert configured.status_code == 200
            assert KEY not in configured.text and configured.json()["llm_provider"] == "openai"
            assert configured.json()["embedding_provider"] == "openai"
            assert configured.json()["local_ai_enabled"] is False
            assert configured.json()["ai_processing_since"]
            with db() as session:
                record = session.get(AIConfiguration, "openai")
                assert record and record.encrypted_key and KEY not in record.encrypted_key
                ciphertext, since = record.encrypted_key, record.processing_since
                assert runtime_settings(session).openai_api_key.get_secret_value() == KEY
            # A fresh process configuration/session can recover the encrypted key.
            get_settings.cache_clear()
            with db() as session:
                assert runtime_settings(session).openai_api_key.get_secret_value() == KEY
            reject = True
            failed = client.post("/api/v1/settings/openai", headers=HEADERS, json={"api_key": KEY + "-replacement", "consent": True})
            assert failed.status_code == 503 and KEY not in failed.text
            assert "chave inválida" in failed.json()["error"]["message"]
            with db() as session:
                record = session.get(AIConfiguration, "openai")
                assert record and record.encrypted_key == ciphertext and record.processing_since == since
            removed = client.delete("/api/v1/settings/openai", headers=HEADERS)
            assert removed.status_code == 200 and removed.json()["external_ai_enabled"] is False
            assert removed.json()["embedding_provider"] == "mock"
            with db() as session:
                record = session.get(AIConfiguration, "openai")
                assert record and record.encrypted_key is None and not record.enabled
                assert not runtime_settings(session).ai_background_enabled
            # Tampering/missing encryption keys never selects a fallback provider.
            with db.begin() as session:
                record = session.get(AIConfiguration, "openai")
                assert record
                record.enabled, record.encrypted_key = True, ciphertext
            key_file.write_bytes(b"x" * 32)
            with db() as session, pytest.raises(ProviderError, match="cannot be decrypted"):
                runtime_settings(session)
    finally:
        get_settings.cache_clear()


def test_new_only_scope_skips_old_backlog_in_worker_and_indexer(db: sessionmaker[Session]) -> None:
    started = datetime.now(UTC)
    with db.begin() as session:
        old = commit_import(session, parse_export(b"05/11/2026, 10:00 - Alice: Eu moro em Recife.", "DMY", "UTC"), "old.txt", "old fixture", None)
        old.created_at = started - timedelta(days=1)
        new = commit_import(session, parse_export(b"05/11/2026, 11:00 - Bob: Eu moro em Natal.", "DMY", "UTC"), "new.txt", "new fixture", None)
        new.created_at = started + timedelta(seconds=1)
        historical = commit_import(session, parse_export(b"05/11/2026, 09:00 - Carol: Eu moro em Olinda.", "DMY", "UTC"), "history.txt", "history fixture", None)
        historical.created_at = started + timedelta(seconds=1)
        historical.source_kind = "whatsapp_history"
    settings = get_settings().model_copy(update={"ai_processing_since": started})
    embeddings = MockEmbeddingProvider(256)
    assert asyncio.run(index_once(db, settings, embeddings)) == 1
    assert run_once(db, settings, MockLLMProvider(), embeddings)
    assert not run_once(db, settings, MockLLMProvider(), embeddings)
    with db() as session:
        jobs = {job.filename: job for job in session.scalars(select(ImportJob))}
        assert jobs["old.txt"].status == "pending" and jobs["old.txt"].processed_messages == 0
        assert jobs["new.txt"].status == "completed"
        assert jobs["history.txt"].status == "pending" and jobs["history.txt"].processed_messages == 0
        assert len(list(session.scalars(select(Message)))) == 3
    disabled = settings.model_copy(update={"ai_background_enabled": False})
    assert not run_once(db, disabled, MockLLMProvider(), embeddings)
    assert asyncio.run(index_once(db, disabled, embeddings)) == 0


@pytest.mark.parametrize("stale_revision", [False, True])
def test_new_native_work_can_index_an_old_original_only_at_its_current_revision(db: sessionmaker[Session], stale_revision: bool) -> None:
    started = datetime.now(UTC)
    with db.begin() as session:
        old = commit_import(session, parse_export(b"05/11/2026, 10:00 - Alice: Eu moro em Recife.\n05/11/2026, 10:01 - Bob: Eu moro em Olinda.", "DMY", "UTC"), "native-history.txt", "native scope fixture", None)
        old.created_at = started - timedelta(days=1)
        old.source_kind = "whatsapp_history"
        row = session.scalar(select(Message).where(Message.source_import_id == old.id).order_by(Message.sequence))
        assert row
        row.origin = "whatsapp"
        row.revision = 2
        row.content = "Eu moro em Natal."
        new = ImportJob(conversation_id=old.conversation_id, filename="native update", source_kind="whatsapp_realtime", status="pending", total_messages=1, imported_messages=1, processed_messages=0, attempts=0, created_at=started + timedelta(seconds=1))
        session.add(new)
        session.flush()
        session.add(MessageWorkItem(job_id=new.id, message_id=row.id, revision=1 if stale_revision else 2))
    settings = get_settings().model_copy(update={"ai_processing_since": started})
    class ObservedEmbedding(MockEmbeddingProvider):
        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            assert texts == ["Eu moro em Natal."]
            return await super().embed_texts(texts)
    embeddings = ObservedEmbedding(256)
    assert asyncio.run(index_once(db, settings, embeddings)) == (0 if stale_revision else 1)
    assert run_once(db, settings, MockLLMProvider(), MockEmbeddingProvider(256))
    with db() as session:
        jobs = {job.filename: job for job in session.scalars(select(ImportJob))}
        assert jobs["native-history.txt"].status == "pending"
        assert jobs["native update"].status == "completed"
