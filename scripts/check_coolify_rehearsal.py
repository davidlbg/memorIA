"""Behavior checks; run ONLY inside the disposable rehearsal backend."""

import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from app.config import get_settings
from app.core.ingestion.parser import parse_export
from app.database.session import session_factory
from app.models.entities import AIConfiguration, Memory, MemoryEmbedding, Message, MessageEmbedding
from app.services.imports import commit_import
from sqlalchemy import func, select, text


def main() -> None:
    if not __debug__:
        raise RuntimeError("Rehearsal verification must run without Python optimization")
    if os.environ.get("MEMORIA_REHEARSAL") != "true":
        raise RuntimeError("Refusing to operate outside an explicit rehearsal")
    settings = get_settings()
    assert settings.llm_provider == settings.embedding_provider == "mock"
    assert settings.external_ai_enabled is False
    assert settings.embedding_dimensions == 1024
    assert os.environ["CONNECTOR_NETWORK_ENABLED"] == "false"
    assert os.environ["DATABASE_URL"].endswith("/memory_rehearsal")
    # The Docker network is internal, independently of provider configuration.
    try:
        connection = socket.create_connection(("1.1.1.1", 443), timeout=2)
    except OSError:
        print("Outbound TCP blocked by isolated network")
    else:
        connection.close()
        raise RuntimeError("Rehearsal unexpectedly has outbound internet access")

    with session_factory()() as session:
        if session.execute(select(func.count()).select_from(AIConfiguration)).scalar_one() != 0:
            raise RuntimeError("Saved provider credentials are forbidden in fixture database")
    headers = {"Authorization": "Bearer " + settings.auth_secret.get_secret_value()}
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=headers, timeout=10) as client:
        response = client.get("/api/v1/health")
        response.raise_for_status()
        effective = response.json()
        assert effective["llm_provider"] == effective["embedding_provider"] == "mock"
        assert effective["external_ai_enabled"] is False
        assert effective["embedding_identity"] == "mock:hash-v1:1024"
        assert client.get("/api/v1/health", headers={"Authorization": ""}).status_code == 401

        def ingest(_: int) -> str:
            content = (
                "01/11/2026, 10:00 - Ensaio: Eu trabalho como engenheiro.\n"
                "01/11/2026, 10:01 - Ensaio: Eu prometo enviar o documento sexta."
            )
            with session_factory().begin() as session:
                job = commit_import(
                    session, parse_export(content.encode(), "DMY", "UTC"),
                    "rehearsal.txt", "Isolated migration rehearsal", None,
                )
                return str(job.conversation_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            conversations = list(pool.map(ingest, range(2)))
        assert len(set(conversations)) == 1

        deadline = time.monotonic() + 90
        while True:
            with session_factory()() as session:
                counts = {
                    "messages": session.execute(select(func.count()).select_from(Message)).scalar_one(),
                    "embeddings": session.execute(select(func.count()).select_from(MessageEmbedding)).scalar_one(),
                    "memories": session.execute(select(func.count()).select_from(Memory)).scalar_one(),
                    "memory_embeddings": session.execute(select(func.count()).select_from(MemoryEmbedding)).scalar_one(),
                }
                assert counts["messages"] == 2
                if all(count == 2 for count in counts.values()):
                    break
            if time.monotonic() >= deadline:
                raise RuntimeError("Synthetic work did not finish: " + json.dumps(counts))
            time.sleep(1)

        for endpoint in ("messages/search", "memories/search"):
            response = client.get("/api/v1/" + endpoint, params={"query": "engenheiro"})
            response.raise_for_status()
            assert response.json(), endpoint
        with httpx.Client(timeout=10) as control:
            response = control.get("http://127.0.0.1:3001/health", headers={
                "Authorization": "Bearer " + settings.connector_secret.get_secret_value(),
            })
            response.raise_for_status()
            connector_before = response.json()
            assert connector_before["network_enabled"] is False
            assert connector_before["credentials_present"] is False
            assert connector_before["qr_data_url"] is None
            response = control.post("http://127.0.0.1:3001/connect", headers={
                "Authorization": "Bearer " + settings.connector_secret.get_secret_value(),
            })
            assert response.status_code == 409

    with session_factory()() as session:
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0009"
        ids = [str(value) for value in session.scalars(select(Message.id).order_by(Message.id))]
        identity: list[str] = list(session.execute(text("SELECT DISTINCT model_name FROM message_embeddings")).scalars().all())
        assert identity == ["mock:hash-v1:1024"]
        for table in ("message_embeddings", "memory_embeddings"):
            table_identity: list[str] = list(session.execute(text(f"SELECT DISTINCT model_name FROM {table}")).scalars().all())
            assert table_identity == ["mock:hash-v1:1024"], table
            dimensions: list[int] = list(session.execute(text(f"SELECT DISTINCT vector_dims(embedding) FROM {table}")).scalars().all())
            assert dimensions == [1024], table
    result = {"counts": counts, "message_ids": ids, "connector": connector_before}
    Path("/tmp/rehearsal-observation.json").write_text(json.dumps(result))
    print("REHEARSAL_BEHAVIOR_PASSED", json.dumps(counts))


if __name__ == "__main__":
    main()
