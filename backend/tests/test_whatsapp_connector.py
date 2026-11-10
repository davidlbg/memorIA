import asyncio
import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
    Contact,
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


@contextmanager
def approved_alias_group(monkeypatch: pytest.MonkeyPatch, account: str = ACCOUNT) -> Iterator[None]:
    with monkeypatch.context() as patch:
        patch.setenv("WHATSAPP_CHAT_ALIAS_GROUPS", json.dumps([{
            "account_id": account,
            "pn_jid": "5511999999999@s.whatsapp.net",
            "lid_jid": "888@lid",
        }]))
        get_settings.cache_clear()
        try:
            yield
        finally:
            get_settings.cache_clear()


def test_explicit_pn_lid_reconciliation_preserves_both_histories(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        deliver(client, owner, event(message_id="PN_ORIGINAL")).raise_for_status()
        deliver(client, owner, event(
            message_id="LID_ORIGINAL", chat_jid="888@lid", sender_jid="888@lid",
        )).raise_for_status()
        with db() as session:
            originals = {m.external_message_id: (m.id, m.conversation_id, m.content)
                         for m in session.scalars(select(Message))}
            conversations = set(session.scalars(select(Conversation.id)))
        joined = event(message_id="PAIR_PROOF", chat_jid="888@lid",
                       chat_jid_alt="5511999999999@s.whatsapp.net", sender_jid="888@lid")
        assert deliver(client, owner, joined).status_code == 409
        with approved_alias_group(monkeypatch):
            response = deliver(client, owner, joined)
            assert response.status_code == 200, response.text
            assert response.json()["accepted"] == 1
            replay = event(message_id="LID_ORIGINAL", origin="whatsapp_history")
            response = deliver(client, owner, replay)
            assert response.status_code == 200, response.text
            assert response.json()["ignored"] == 1
        with db() as session:
            assert set(session.scalars(select(Conversation.id))) == conversations
            for key, (identifier, conversation, content) in originals.items():
                message = session.get(Message, identifier)
                assert message is not None
                assert (message.external_message_id, message.conversation_id, message.content) == (
                    key, conversation, content,
                )
            assert session.scalar(select(func.count()).select_from(Message)) == 3
            added = session.scalar(select(Message).where(Message.external_message_id == "PAIR_PROOF"))
            assert added is not None and added.conversation_id == originals["LID_ORIGINAL"][1]


def test_reconciled_aliases_edit_and_revoke_in_the_original_conversation(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        deliver(client, owner, event(message_id="PN")).raise_for_status()
        deliver(client, owner, event(message_id="LID", chat_jid="888@lid", sender_jid="888@lid")).raise_for_status()
        with db() as session:
            before = {m.external_message_id: (m.id, m.conversation_id, m.sender_contact_id)
                      for m in session.scalars(select(Message))}
        with approved_alias_group(monkeypatch):
            response = deliver(client, owner, event(message_id="LID", event_type="message.edit",
                version_ms=BASE_VERSION + 1000, text="Eu trabalho como médica."))
            assert response.status_code == 200, response.text
            response = deliver(client, owner, event(message_id="PN", chat_jid="888@lid",
                sender_jid="888@lid", event_type="message.revoke", text="", version_ms=None))
            assert response.status_code == 200, response.text
        with db() as session:
            for key, identity in before.items():
                m = session.get(Message, identity[0])
                assert m is not None and (m.id, m.conversation_id, m.sender_contact_id) == identity
                assert m.content == ("" if key == "PN" else "Eu trabalho como médica.")


def test_reconciled_aliases_concurrently_deduplicate_a_native_message(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        deliver(client, owner, event(message_id="FIRST_PN")).raise_for_status()
        deliver(client, owner, event(message_id="FIRST_LID", chat_jid="888@lid", sender_jid="888@lid")).raise_for_status()
    bodies = [EventBatch.model_validate({**owner, "items": [{"sequence": 1, "event": item}]}) for item in (
        event(message_id="CONCURRENT"),
        event(message_id="CONCURRENT", chat_jid="888@lid", sender_jid="888@lid"),
    )]
    def submit(body: EventBatch) -> int:
        with db.begin() as session:
            return ingest_events(session, body).accepted
    with approved_alias_group(monkeypatch), ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, bodies)) == [0, 1]
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Message).where(Message.external_message_id == "CONCURRENT")) == 1
        assert session.scalar(select(func.count()).select_from(Conversation)) == 2


def test_reconciliation_preserves_deleted_history_tombstones(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        deliver(client, owner, event(message_id="PN_KEEP")).raise_for_status()
        deliver(client, owner, event(message_id="LID_DELETE", chat_jid="888@lid", sender_jid="888@lid")).raise_for_status()
        with db() as session:
            conversation = session.scalar(select(Message.conversation_id).where(Message.external_message_id == "LID_DELETE"))
        client.delete(f"/api/v1/conversations/{conversation}", headers=public_headers()).raise_for_status()
        with approved_alias_group(monkeypatch):
            response = deliver(client, owner, event(message_id="LID_DELETE", origin="whatsapp_history"))
            assert response.status_code == 200, response.text
            assert response.json()["ignored"] == 1
        with db() as session:
            assert session.scalar(select(func.count()).select_from(Conversation)) == 1
            assert session.scalar(select(func.count()).select_from(Message)) == 1


def test_ambiguous_reconciled_message_rolls_back_the_whole_batch(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        deliver(client, owner, event(message_id="COLLISION")).raise_for_status()
        deliver(client, owner, event(message_id="COLLISION", chat_jid="888@lid", sender_jid="888@lid")).raise_for_status()
        with approved_alias_group(monkeypatch):
            response = deliver(client, owner, event(message_id="MUST_ROLL_BACK"),
                               event(message_id="COLLISION", origin="whatsapp_history"))
            assert response.status_code == 409
        with db() as session:
            assert session.scalar(select(func.count()).select_from(Message)) == 2
            assert session.scalar(select(func.count()).select_from(WhatsAppEventRecord)) == 2
            assert all(c.external_alias is None for c in session.scalars(select(Contact)))


def test_alias_approval_does_not_cross_accounts_or_accept_a_third_identity(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        deliver(client, owner, event(message_id="PN")).raise_for_status()
        deliver(client, owner, event(message_id="LID", chat_jid="888@lid", sender_jid="888@lid")).raise_for_status()
        joined = event(message_id="NO_APPROVAL", chat_jid_alt="888@lid")
        with approved_alias_group(monkeypatch, account="b" * 64):
            assert deliver(client, owner, joined).status_code == 409
        with approved_alias_group(monkeypatch):
            assert deliver(client, owner, event(message_id="THIRD", chat_jid_alt="999@lid")).status_code == 409
        with db() as session:
            assert session.scalar(select(func.count()).select_from(Message)) == 2


def test_peer_alias_is_not_assigned_to_our_outgoing_contact(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        with approved_alias_group(monkeypatch):
            deliver(client, owner, event(from_me=True, sender_jid="5511888888888@s.whatsapp.net",
                                       chat_jid_alt="888@lid")).raise_for_status()
        with db() as session:
            contact = session.scalar(select(Contact))
            assert contact is not None and contact.external_identifier == "5511888888888@s.whatsapp.net"
            assert contact.external_alias is None


def test_explicit_reconciliation_preserves_legacy_duplicate_peer_contacts(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        deliver(client, owner, event(message_id="PN_CONTACT")).raise_for_status()
        # The old connector could learn the chat alias on an outgoing message,
        # then create a second peer contact when an unpaired LID event arrived.
        deliver(client, owner, event(message_id="SELF", from_me=True, chat_jid="888@lid",
            chat_jid_alt="5511999999999@s.whatsapp.net", sender_jid="5511888888888@s.whatsapp.net")).raise_for_status()
        deliver(client, owner, event(message_id="LID_CONTACT", chat_jid="888@lid", sender_jid="888@lid")).raise_for_status()
        with db() as session:
            contacts = {c.external_identifier: c.id for c in session.scalars(select(Contact))}
            assert len(contacts) == 3
            identities = {m.external_message_id: (m.id, m.conversation_id, m.sender_contact_id)
                          for m in session.scalars(select(Message))}
        with approved_alias_group(monkeypatch):
            response = deliver(client, owner, event(message_id="NEW_CONTACT_EVENT", chat_jid="888@lid",
                sender_jid="888@lid", sender_jid_alt="5511999999999@s.whatsapp.net"))
            assert response.status_code == 200, response.text
            response = deliver(client, owner, event(message_id="PN_CONTACT", chat_jid="888@lid",
                sender_jid="888@lid", event_type="message.edit", version_ms=BASE_VERSION + 1000,
                text="Eu trabalho como arquiteta."))
            assert response.status_code == 200, response.text
        with db() as session:
            assert {c.external_identifier: c.id for c in session.scalars(select(Contact))} == contacts
            for identity in identities.values():
                m = session.get(Message, identity[0])
                assert m is not None and (m.id, m.conversation_id, m.sender_contact_id) == identity
            added = session.scalar(select(Message).where(Message.external_message_id == "NEW_CONTACT_EVENT"))
            assert added is not None and added.sender_contact_id == contacts["888@lid"]


def test_unapproved_contacts_keep_native_sender_routing_with_a_chat_alternate(
    db: sessionmaker[Session],
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        deliver(client, owner, event(message_id="PN_PEER")).raise_for_status()
        deliver(client, owner, event(message_id="SELF_LEARNS_CHAT", from_me=True,
            chat_jid="888@lid", chat_jid_alt="5511999999999@s.whatsapp.net",
            sender_jid="5511888888888@s.whatsapp.net")).raise_for_status()
        deliver(client, owner, event(message_id="LID_PEER", chat_jid="888@lid", sender_jid="888@lid")).raise_for_status()
        with db() as session:
            before = {c.external_identifier: (c.id, c.external_alias) for c in session.scalars(select(Contact))}
        response = deliver(client, owner, event(message_id="UNAPPROVED_CHAT_ALT", chat_jid="888@lid",
            chat_jid_alt="5511999999999@s.whatsapp.net", sender_jid="888@lid"))
        assert response.status_code == 200, response.text
        with db() as session:
            assert {c.external_identifier: (c.id, c.external_alias) for c in session.scalars(select(Contact))} == before


@pytest.mark.parametrize("outgoing", [False, True])
def test_approved_peer_identifier_cannot_be_claimed_as_a_third_senders_alternate(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, outgoing: bool,
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        deliver(client, owner, event(message_id="PEER")).raise_for_status()
        with db() as session:
            original = session.scalar(select(Contact))
            assert original is not None
            identity = (original.id, original.external_identifier, original.external_alias)
        with approved_alias_group(monkeypatch):
            response = deliver(client, owner, event(message_id="THIRD_SENDER", from_me=outgoing,
                sender_jid="5511888888888@s.whatsapp.net", sender_jid_alt="5511999999999@s.whatsapp.net"))
            assert response.status_code == 409
        with db() as session:
            contact = session.get(Contact, identity[0])
            assert contact is not None and (contact.id, contact.external_identifier, contact.external_alias) == identity
            assert session.scalar(select(func.count()).select_from(Message)) == 1


def test_chat_with_a_stored_third_alias_cannot_reconcile_cross_history_revocations(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as client:
        owner = lease(client)
        deliver(client, owner, event(message_id="PN")).raise_for_status()
        deliver(client, owner, event(message_id="THIRD_HISTORY", chat_jid="999@lid",
            chat_jid_alt="5511999999999@s.whatsapp.net", sender_jid="999@lid")).raise_for_status()
        deliver(client, owner, event(message_id="LID", chat_jid="888@lid", sender_jid="888@lid")).raise_for_status()
        with approved_alias_group(monkeypatch):
            response = deliver(client, owner, event(message_id="THIRD_HISTORY", chat_jid="888@lid",
                sender_jid="888@lid", event_type="message.revoke", text="", version_ms=None))
            assert response.status_code == 409
        with db() as session:
            message = session.scalar(select(Message).where(Message.external_message_id == "THIRD_HISTORY"))
            assert message is not None and message.delivery_state == "active" and message.content


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
