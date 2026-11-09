import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider, ProviderError
from app.main import create_app
from app.models.entities import (
    Conversation,
    ImportJob,
    Memory,
    MemorySource,
    Message,
    MessageEmbedding,
    MessageWorkItem,
    WhatsAppConnection,
    WhatsAppEventRecord,
)
from app.schemas.domain import Extraction, GeneratedAnswer, Query, SourceMessage
from app.schemas.whatsapp import EventBatch
from app.services.knowledge import answer_query
from app.services.whatsapp import ingest_events
from app.workers.runner import run_once

pytestmark = pytest.mark.database
STAMP = datetime(2026, 11, 3, 12, tzinfo=UTC)
BASE_VERSION = int(STAMP.timestamp() * 1000)
ACCOUNT = "a" * 64


def event(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "account_id": ACCOUNT,
        "event_type": "message.upsert",
        "chat_jid": "5511999999999@s.whatsapp.net",
        "message_id": "SYNTHETIC01",
        "sender_jid": "5511999999999@s.whatsapp.net",
        "sender_name": "Contato sintético",
        "from_me": False,
        "sent_at": STAMP.isoformat(),
        "version_ms": BASE_VERSION,
        "message_type": "text",
        "text": "Eu trabalho como engenheira.",
        "origin": "whatsapp_realtime",
        "metadata": {"native_type": "conversation"},
    }
    value.update(changes)
    value["event_id"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().connector_secret.get_secret_value()}"}


def public_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().auth_secret.get_secret_value()}"}


def test_first_account_identity_after_pairing_restart_is_not_an_account_switch(
    db: sessionmaker[Session],
) -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/internal/whatsapp/lease",
            headers=headers(),
            json={"instance_id": str(uuid4()), "run_id": str(uuid4())},
        )
        assert response.status_code == 200
        owner = response.json()
        for state in ("QR_REQUIRED", "RECONNECTING"):
            response = client.post(
                "/internal/whatsapp/connection/status",
                headers=headers(),
                json={**owner, "state": state, "account_id": None},
            )
            assert response.status_code == 200
        response = client.post(
            "/internal/whatsapp/connection/status",
            headers=headers(),
            json={**owner, "state": "INITIALIZING", "account_id": ACCOUNT},
        )
        assert response.status_code == 200
        client.post(
            "/internal/whatsapp/connection/status",
            headers=headers(),
            json={**owner, "state": "RECONNECTING", "account_id": ACCOUNT},
        ).raise_for_status()
        response = client.post(
            "/internal/whatsapp/connection/status",
            headers=headers(),
            json={**owner, "state": "INITIALIZING", "account_id": "b" * 64},
        )
        assert response.status_code == 409

        # A deliberate new QR starts a new unidentified pairing, which may
        # legally restart before its identity becomes available.
        for state in ("QR_REQUIRED", "RECONNECTING"):
            response = client.post(
                "/internal/whatsapp/connection/status",
                headers=headers(),
                json={**owner, "state": state, "account_id": None},
            )
            assert response.status_code == 200
        response = client.post(
            "/internal/whatsapp/connection/status",
            headers=headers(),
            json={**owner, "state": "INITIALIZING", "account_id": "b" * 64},
        )
        assert response.status_code == 200


def lease(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/internal/whatsapp/lease",
        headers=headers(),
        json={"instance_id": str(uuid4()), "run_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (
        client.post(
            "/internal/whatsapp/connection/status",
            headers=headers(),
            json={**body, "state": "CONNECTED", "account_id": ACCOUNT},
        ).status_code
        == 200
    )
    return body


def deliver(client: TestClient, owner: dict[str, object], *events: dict[str, object]):
    return client.post(
        "/internal/whatsapp/events",
        headers=headers(),
        json={
            **owner,
            "items": [{"sequence": index + 1, "event": item} for index, item in enumerate(events)],
        },
    )


def process(db: sessionmaker[Session]) -> None:
    for _ in range(20):
        if not run_once(db, get_settings(), MockLLMProvider(), MockEmbeddingProvider(256)):
            return
    raise AssertionError("Connector queue did not drain")


def test_token_scope_locality_and_strict_payload_validation(db: sessionmaker[Session]) -> None:
    with TestClient(create_app()) as client:
        assert (
            client.post("/internal/whatsapp/lease", headers=public_headers(), json={}).status_code
            == 401
        )
        assert client.get("/api/v1/conversations", headers=headers()).status_code == 401
        owner = lease(client)
        bad = event(
            text="never-log-this", metadata={"native_type": "text", "mediaKey": "sensitive-key"}
        )
        response = deliver(client, owner, bad)
        assert response.status_code == 422
        assert "never-log-this" not in response.text and "sensitive-key" not in response.text
        assert deliver(client, owner, event(account_id="b" * 64)).status_code == 403
    with TestClient(create_app(), client=("203.0.113.1", 1234)) as client:
        assert (
            client.post(
                "/internal/whatsapp/lease",
                headers=headers(),
                json={"instance_id": str(uuid4()), "run_id": str(uuid4())},
            ).status_code
            == 403
        )


def test_native_id_deduplication_works_with_worker_and_assistant(db: sessionmaker[Session]) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        first = deliver(client, owner, event())
        assert first.status_code == 200, first.text
        assert deliver(client, owner, event()).json()["duplicates"] == 1
        assert deliver(client, owner, event(origin="whatsapp_history")).json()["ignored"] == 1
        process(db)
        answer = client.post(
            "/api/v1/assistant/query", headers=public_headers(), json={"question": "engenheira"}
        )
        assert answer.status_code == 200, answer.text
        assert answer.json()["sources"][0]["content"] == "Eu trabalho como engenheira."
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 1
        assert session.scalar(select(func.count()).select_from(Memory)) == 1
        assert session.scalar(select(func.count()).select_from(MessageWorkItem)) == 1
        assert session.scalar(select(func.count()).select_from(ImportJob)) == 1
        assert all(row.payload == {} for row in session.scalars(select(WhatsAppEventRecord)))


def test_capture_and_edit_proceed_during_inference_and_stale_derivatives_are_rejected(
    db: sessionmaker[Session],
) -> None:
    entered, release = Event(), Event()

    class WaitingLLM(MockLLMProvider):
        async def extract_memories(self, messages: list[SourceMessage]) -> Extraction:
            entered.set()
            if not release.wait(timeout=20):
                raise RuntimeError("Synthetic inference was not released")
            return await super().extract_memories(messages)

    with TestClient(create_app()) as client, ThreadPoolExecutor(max_workers=2) as executor:
        owner = lease(client)
        assert deliver(client, owner, event()).status_code == 200
        working = executor.submit(
            run_once, db, get_settings(), WaitingLLM(), MockEmbeddingProvider(256)
        )
        try:
            assert entered.wait(timeout=10)
            # A second worker cannot claim this same conversation.
            assert not run_once(db, get_settings(), MockLLMProvider(), MockEmbeddingProvider(256))
            delivered = executor.submit(
                deliver,
                client,
                owner,
                event(message_id="SYNTHETIC02", text="Mensagem nova durante inferência."),
                event(
                    event_type="message.edit",
                    text="Eu trabalho como médica.",
                    version_ms=BASE_VERSION + 5000,
                ),
            ).result(timeout=10)
            assert delivered.status_code == 200
        finally:
            release.set()
        assert working.result(timeout=10)
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 2
        assert session.scalar(select(func.count()).select_from(Memory)) == 0
        assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 0
        assert any(
            job.status == "pending" and job.attempts == 1
            for job in session.scalars(select(ImportJob))
        )
    process(db)
    with db() as session:
        assert list(session.scalars(select(Memory.content))) == ["Eu trabalho como médica."]


def test_edit_and_revoke_purge_old_sources_vectors_and_pending_work(
    db: sessionmaker[Session],
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        assert deliver(client, owner, event()).status_code == 200
        process(db)
        edited = event(
            event_type="message.edit",
            text="Eu trabalho como médica.",
            version_ms=BASE_VERSION + 5000,
        )
        assert deliver(client, owner, edited).status_code == 200
        with db() as session:
            assert session.scalar(select(func.count()).select_from(Memory)) == 0
            assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 0
        process(db)
        assert (
            client.get("/api/v1/messages/search?query=engenheira", headers=public_headers()).json()
            == []
        )
        assert (
            len(client.get("/api/v1/messages/search?query=médica", headers=public_headers()).json())
            == 1
        )
        revoked = event(event_type="message.revoke", text="", version_ms=None)
        assert deliver(client, owner, revoked).status_code == 200
        assert deliver(client, owner, event()).status_code == 200
        process(db)
        assert client.get("/api/v1/messages/search", headers=public_headers()).json() == []
        with db() as session:
            message = session.scalar(select(Message))
            assert message and message.content == "" and message.delivery_state == "revoked"
            assert message.connector_metadata == {}
            assert session.scalar(select(func.count()).select_from(MemorySource)) == 0


def test_edit_before_original_and_stale_worker_versions(db: sessionmaker[Session]) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        edit = event(
            event_type="message.edit",
            text="Eu trabalho como médica.",
            version_ms=BASE_VERSION + 5000,
        )
        assert deliver(client, owner, edit).status_code == 200
        with db() as session:
            assert session.scalar(select(func.count()).select_from(Message)) == 0
        assert deliver(client, owner, event()).status_code == 200
        process(db)
        with db() as session:
            assert session.scalar(select(Message.content)) == "Eu trabalho como médica."
            assert list(session.scalars(select(Memory.content))) == ["Eu trabalho como médica."]
            message = session.scalar(select(Message))
            memory = session.scalar(select(Memory))
            assert message and memory
            assert message.sent_at == STAMP
            assert memory.observed_at == STAMP + timedelta(seconds=5)
            assert all(job.status == "completed" for job in session.scalars(select(ImportJob)))
            assert all(
                record.payload == {} for record in session.scalars(select(WhatsAppEventRecord))
            )


@pytest.mark.parametrize("mutation", ["edit", "revoke"])
def test_answer_rejects_evidence_edited_during_generation(
    db: sessionmaker[Session], mutation: str
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        assert deliver(client, owner, event()).status_code == 200
        process(db)

        class EditingProvider(MockLLMProvider):
            async def generate(self, prompt: str, context: list[SourceMessage]) -> GeneratedAnswer:
                # Commit an actual edit in another transaction while the answer
                # still holds the previously retrieved context.
                assert (
                    deliver(
                        client,
                        owner,
                        event(
                            event_type="message." + mutation,
                            text="Texto atualizado." if mutation == "edit" else "",
                            version_ms=BASE_VERSION + 5000 if mutation == "edit" else None,
                        ),
                    ).status_code
                    == 200
                )
                return await super().generate(prompt, context)

        with db.begin() as session:
            with pytest.raises(ProviderError, match="Evidence changed"):
                asyncio.run(
                    answer_query(
                        session,
                        get_settings(),
                        EditingProvider(),
                        MockEmbeddingProvider(256),
                        Query(question="engenheira"),
                    )
                )


def test_internal_ack_is_not_sent_before_transaction_finalization(
    db: sessionmaker[Session],
) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.api.dependencies import database

    app = create_app()
    with TestClient(app) as client:
        owner = lease(client)

        def fail_commit():
            with db.begin() as session:
                yield session
                raise IntegrityError("synthetic deferred constraint", {}, RuntimeError("fixture"))

        app.dependency_overrides[database] = fail_commit
        response = deliver(client, owner, event())
        assert response.status_code == 503
        app.dependency_overrides.clear()
        with db() as session:
            assert session.scalar(select(func.count()).select_from(Message)) == 0
        assert deliver(client, owner, event()).json()["accepted"] == 1


def test_revoke_before_original_never_resurrects_text(db: sessionmaker[Session]) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        assert (
            deliver(
                client,
                owner,
                event(
                    event_type="message.edit", text="Eu sou médica.", version_ms=BASE_VERSION + 5000
                ),
            ).status_code
            == 200
        )
        assert (
            deliver(
                client, owner, event(event_type="message.revoke", text="", version_ms=None)
            ).status_code
            == 200
        )
        assert deliver(client, owner, event()).json()["ignored"] == 1
        with db() as session:
            assert session.scalar(select(func.count()).select_from(Message)) == 0
            assert all(
                record.payload == {} for record in session.scalars(select(WhatsAppEventRecord))
            )


def test_accounts_and_proven_chat_aliases_are_separate(db: sessionmaker[Session]) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        assert (
            deliver(
                client,
                owner,
                event(chat_jid="888@lid", chat_jid_alt="5511999999999@s.whatsapp.net"),
            ).status_code
            == 200
        )
        assert (
            deliver(
                client,
                owner,
                event(
                    chat_jid="5511999999999@s.whatsapp.net",
                    chat_jid_alt="888@lid",
                    origin="whatsapp_history",
                ),
            ).json()["ignored"]
            == 1
        )
        assert (
            client.post(
                "/internal/whatsapp/connection/status",
                headers=headers(),
                json={**owner, "state": "INITIALIZING", "account_id": None},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/internal/whatsapp/connection/status",
                headers=headers(),
                json={**owner, "state": "CONNECTED", "account_id": "b" * 64},
            ).status_code
            == 200
        )
        assert deliver(client, owner, event(account_id="b" * 64)).status_code == 200
        with db() as session:
            assert session.scalar(select(func.count()).select_from(Conversation)) == 2
            assert session.scalar(select(func.count()).select_from(Message)) == 2


def test_replay_payload_collision_and_expired_fence_rejected(db: sessionmaker[Session]) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        original = event()
        assert deliver(client, owner, original).status_code == 200
        modified = {**original, "text": "invented replacement"}
        assert deliver(client, owner, modified).status_code == 409
        with db.begin() as session:
            row = session.scalar(select(WhatsAppConnection))
            assert row
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        replacement = client.post(
            "/internal/whatsapp/lease",
            headers=headers(),
            json={"instance_id": str(uuid4()), "run_id": str(uuid4())},
        )
        assert replacement.status_code == 200
        assert deliver(client, owner, event(message_id="STALE")).status_code == 409
        assert (
            client.post(
                "/internal/whatsapp/lease",
                headers=headers(),
                json={"instance_id": str(uuid4()), "run_id": str(uuid4())},
            ).status_code
            == 409
        )


def test_concurrent_replayed_delivery_is_atomic(db: sessionmaker[Session]) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
    body = EventBatch.model_validate({**owner, "items": [{"sequence": 1, "event": event()}]})

    def submit(_: int) -> int:
        with db.begin() as session:
            return ingest_events(session, body).duplicates

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, range(2))) == [0, 1]
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 1


def test_user_deleted_conversation_is_not_restored_by_replay(db: sessionmaker[Session]) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        assert deliver(client, owner, event()).status_code == 200
        conversation = client.get("/api/v1/conversations", headers=public_headers()).json()[0]
        assert (
            client.delete(
                f"/api/v1/conversations/{conversation['id']}", headers=public_headers()
            ).status_code
            == 200
        )
        assert deliver(client, owner, event(origin="whatsapp_history")).json()["ignored"] == 1
        with db() as session:
            assert session.scalar(select(func.count()).select_from(Message)) == 0
            assert session.scalar(select(func.count()).select_from(Conversation)) == 0


def test_same_second_edits_purge_ambiguous_content_without_blocking_other_messages(
    db: sessionmaker[Session],
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        assert deliver(client, owner, event()).status_code == 200
        process(db)
        # A real first edit has causal precedence over the original, even when
        # the SDK rounds both native timestamps to the same second.
        first = event(
            event_type="message.edit", text="Eu trabalho como médica.", version_ms=BASE_VERSION
        )
        assert deliver(client, owner, first).status_code == 200
        process(db)
        with db() as session:
            row = session.scalar(select(Message))
            assert row and row.content == "Eu trabalho como médica." and row.revision == 2
        ambiguous = event(
            event_type="message.edit", text="Eu trabalho como enfermeira.", version_ms=BASE_VERSION
        )
        other = event(message_id="OTHER-SYNTHETIC", text="Eu moro em Recife.")
        assert deliver(client, owner, ambiguous, other).status_code == 200
        process(db)
        with db() as session:
            row = session.scalar(
                select(Message).where(Message.external_message_id == "SYNTHETIC01")
            )
            assert (
                row
                and row.content == ""
                and row.connector_metadata == {}
                and row.delivery_state == "conflicted"
            )
            assert list(session.scalars(select(Memory.content))) == ["Eu moro em Recife."]
            assert all(
                record.payload == {} for record in session.scalars(select(WhatsAppEventRecord))
            )
        resolved = event(
            event_type="message.edit",
            text="Eu trabalho como médica.",
            version_ms=BASE_VERSION + 5000,
        )
        assert deliver(client, owner, resolved).status_code == 200
        assert deliver(client, owner, ambiguous).status_code == 200
        process(db)
        with db() as session:
            row = session.scalar(
                select(Message).where(Message.external_message_id == "SYNTHETIC01")
            )
            assert (
                row and row.delivery_state == "active" and row.content == "Eu trabalho como médica."
            )


def test_same_second_edit_before_original_applies_without_false_conflict(
    db: sessionmaker[Session],
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        edit = event(
            event_type="message.edit", text="Eu trabalho como médica.", version_ms=BASE_VERSION
        )
        assert deliver(client, owner, edit).status_code == 200
        assert deliver(client, owner, event()).status_code == 200
        process(db)
        with db() as session:
            row = session.scalar(select(Message))
            assert (
                row and row.delivery_state == "active" and row.content == "Eu trabalho como médica."
            )
