import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.memory.pipeline import consolidate
from app.models.entities import (
    Conversation,
    ImportJob,
    Memory,
    MemoryContext,
    MemorySource,
    Message,
    MessageEmbedding,
    MessageWorkItem,
    WhatsAppChat,
    WhatsAppChatAlias,
    WhatsAppConnection,
    WhatsAppEventRecord,
    WhatsAppMessageState,
    now,
)
from app.schemas.whatsapp import (
    ClaimLease,
    ConnectionUpdate,
    DeliveryResult,
    EventBatch,
    Lease,
    MessageEvent,
)
from app.services.persistence import ensure_contact, ensure_conversation, persist_message

LEASE_SECONDS = 90


def claim(session: Session, request: ClaimLease) -> Lease:
    session.execute(
        insert(WhatsAppConnection)
        .values(
            slot="local",
            instance_id=request.instance_id,
            run_id=request.run_id,
            lease_expires_at=now() + timedelta(seconds=LEASE_SECONDS),
        )
        .on_conflict_do_nothing(index_elements=["slot"])
    )
    connection = session.scalar(
        select(WhatsAppConnection).where(WhatsAppConnection.slot == "local").with_for_update()
    )
    if connection is None:
        raise RuntimeError("Connection slot missing")
    if connection.run_id != request.run_id:
        if connection.lease_expires_at > now():
            raise HTTPException(409, "Outro processo do conector mantém a sessão")
        connection.fence += 1
        connection.last_sequence = 0
        connection.state = "DISCONNECTED"
    connection.instance_id = request.instance_id
    connection.run_id = request.run_id
    connection.lease_expires_at = now() + timedelta(seconds=LEASE_SECONDS)
    return Lease(run_id=connection.run_id, fence=connection.fence)


def require_lease(session: Session, lease: Lease) -> WhatsAppConnection:
    row = session.scalar(
        select(WhatsAppConnection).where(WhatsAppConnection.slot == "local").with_for_update()
    )
    if (
        row is None
        or row.run_id != lease.run_id
        or row.fence != lease.fence
        or row.lease_expires_at <= now()
    ):
        raise HTTPException(409, "Lease do conector expirou ou foi substituído")
    return row


def update_status(session: Session, request: ConnectionUpdate) -> None:
    connection = require_lease(session, request)
    if request.state == "QR_REQUIRED" and request.account_id is None:
        # A fresh QR has no identified account yet. The connector refuses a
        # new pairing while the previous account still has an outbox to drain.
        # Clear only the current slot's identity, never its stored history.
        connection.account_id = None
    if request.account_id and request.account_id != connection.account_id:
        if connection.account_id is not None and connection.state not in (
            "DISCONNECTED",
            "INITIALIZING",
            "QR_REQUIRED",
            "CONNECTING",
            "LOGGED_OUT",
        ):
            raise HTTPException(409, "Troca de conta exige nova inicialização")
        connection.account_id = request.account_id
    connection.state = request.state
    connection.last_status_at = now()


def chat_for(session: Session, event: MessageEvent) -> tuple[WhatsAppChat, Conversation | None]:
    jids = {value for value in (event.chat_jid, event.chat_jid_alt) if value}
    aliases = list(
        session.scalars(
            select(WhatsAppChatAlias).where(
                WhatsAppChatAlias.account_id == event.account_id, WhatsAppChatAlias.jid.in_(jids)
            )
        )
    )
    chat_ids = {alias.chat_id for alias in aliases}
    if len(chat_ids) > 1:
        raise HTTPException(409, "Conflito PN/LID; reconciliação explícita necessária")
    chat = session.get(WhatsAppChat, next(iter(chat_ids))) if chat_ids else None
    if chat is None:
        chat = WhatsAppChat(account_id=event.account_id)
        session.add(chat)
        session.flush()
    for jid in jids - {alias.jid for alias in aliases}:
        session.add(WhatsAppChatAlias(account_id=event.account_id, jid=jid, chat_id=chat.id))
    conversation = (
        session.scalar(
            select(Conversation).where(Conversation.id == chat.conversation_id).with_for_update()
        )
        if chat.conversation_id
        else None
    )
    if conversation is None:
        removed = session.scalar(
            select(WhatsAppMessageState.id).where(
                WhatsAppMessageState.chat_id == chat.id,
                WhatsAppMessageState.external_message_id == event.message_id,
                WhatsAppMessageState.from_me == event.from_me,
                WhatsAppMessageState.deleted_locally.is_(True),
            )
        )
        if removed is not None:
            return chat, None
        conversation = ensure_conversation(
            session,
            f"wa:{event.account_id}:{chat.id}",
            event.chat_jid,
            "group" if event.chat_jid.endswith("@g.us") else "direct",
        )
        chat.conversation_id = conversation.id
    return chat, conversation


def invalidate_sources(session: Session, message: Message) -> None:
    """Remove derived text before changing an original; preserve user tombstones."""
    dependent_ids = set(
        session.scalars(
            select(MemoryContext.memory_id).where(MemoryContext.message_id == message.id)
        )
    )
    cited_ids = set(
        session.scalars(select(MemorySource.memory_id).where(MemorySource.message_id == message.id))
    )
    memories = list(
        session.scalars(
            select(Memory).where(Memory.id.in_(dependent_ids | cited_ids)).order_by(Memory.id)
        )
    )
    for memory in memories:
        if memory.status == "deleted":
            session.execute(
                update(MemorySource)
                .where(MemorySource.memory_id == memory.id, MemorySource.message_id == message.id)
                .values(evidence_quote="")
            )
            continue
        if memory.id in dependent_ids or memory.evidence_basis == "interpretation":
            session.delete(memory)
            continue
        remaining = list(
            session.scalars(
                select(Message)
                .join(MemorySource, MemorySource.message_id == Message.id)
                .where(
                    MemorySource.memory_id == memory.id,
                    Message.id != message.id,
                    Message.delivery_state == "active",
                )
            )
        )
        valid = [source for source in remaining if memory.content in source.content]
        if not valid:
            session.delete(memory)
        else:
            memory.observed_at = min(
                source.content_updated_at or source.sent_at for source in valid
            )
            memory.evidence_summary = "Fonte atual preservada após alteração de outra mensagem."
            session.execute(
                delete(MemorySource).where(
                    MemorySource.memory_id == memory.id, MemorySource.message_id == message.id
                )
            )
    session.execute(delete(MessageEmbedding).where(MessageEmbedding.message_id == message.id))
    session.flush()
    # Consolidation's only automatic outdated state is explicit cancellation of an
    # intention. Recompute that narrow rule after removal of its evidence.
    intentions = list(
        session.scalars(
            select(Memory).where(
                Memory.conversation_id == message.conversation_id,
                Memory.memory_type == "intention",
                Memory.status.in_(["active", "outdated"]),
            )
        )
    )
    for memory in intentions:
        memory.status = "active"
    session.flush()
    for memory in intentions:
        consolidate(session, memory)


def enqueue_revision(session: Session, message: Message, origin: str) -> None:
    job = ImportJob(
        conversation_id=message.conversation_id,
        filename="WhatsApp · evento",
        source_kind=origin,
        status="pending",
        total_messages=1,
        imported_messages=1,
        processed_messages=0,
        attempts=0,
    )
    session.add(job)
    session.flush()
    session.add(MessageWorkItem(job_id=job.id, message_id=message.id, revision=message.revision))


def apply_event(
    session: Session, state: WhatsAppMessageState, conversation: Conversation, event: MessageEvent
) -> str:
    if state.deleted_locally or state.revoked:
        return "ignored"
    message = session.get(Message, state.message_id) if state.message_id else None
    if event.event_type == "message.revoke":
        state.revoked = True
        for pending in session.scalars(
            select(WhatsAppEventRecord).where(
                WhatsAppEventRecord.message_state_id == state.id,
                WhatsAppEventRecord.outcome == "pending",
            )
        ):
            pending.payload = {}
            pending.outcome = "ignored"
        if message:
            invalidate_sources(session, message)
            message.content = ""
            message.connector_metadata = {}
            message.delivery_state = "revoked"
            message.revision += 1
        return "revoked"
    if message is None:
        if event.event_type != "message.upsert":
            return "pending"
        if event.sent_at is None or event.sender_jid is None:
            raise HTTPException(422, "Mensagem original incompleta")
        contact = ensure_contact(
            session,
            conversation.id,
            event.sender_name or event.sender_jid,
            event.sender_jid,
            event.sender_jid_alt,
        )
        job = ImportJob(
            conversation_id=conversation.id,
            filename="WhatsApp · evento",
            source_kind=event.origin,
            status="pending",
            total_messages=1,
            imported_messages=1,
            processed_messages=0,
            attempts=0,
        )
        session.add(job)
        session.flush()
        message = persist_message(
            session,
            job,
            conversation.id,
            contact.id,
            event.sender_name or event.sender_jid,
            event.text,
            event.message_type,
            datetime.fromisoformat(event.sent_at.replace("Z", "+00:00")),
            hashlib.sha256(f"wa:{state.id}".encode()).hexdigest(),
            0,
            external_id=event.message_id,
            origin=event.origin,
            metadata={**event.metadata.model_dump(exclude_none=True), "from_me": event.from_me},
        )
        if message is None:
            raise HTTPException(409, "Conflito na identidade da mensagem")
        state.message_id = message.id
        state.last_version_ms = event.version_ms
        session.add(
            MessageWorkItem(job_id=job.id, message_id=message.id, revision=message.revision)
        )
        return "created"
    if event.event_type != "message.edit":
        return "ignored"
    if event.version_ms is None or (
        state.last_version_ms is not None and event.version_ms < state.last_version_ms
    ):
        return "ignored"
    if event.sender_jid:
        matched_sender = ensure_contact(
            session,
            conversation.id,
            event.sender_name or message.sender_name_snapshot or event.sender_jid,
            event.sender_jid,
            event.sender_jid_alt,
        )
        if matched_sender.id != message.sender_contact_id:
            raise HTTPException(409, "Edição com remetente incompatível")
    if event.version_ms == state.last_version_ms:
        if event.text == message.content:
            return "ignored"
        if message.revision > 1 or message.delivery_state != "active":
            # A first explicit edit supersedes its original even at second
            # precision. Two different edits at that same precision cannot be
            # ordered safely: purge old content, retain control IDs, and keep
            # unrelated deliveries flowing rather than choosing an arrival order.
            invalidate_sources(session, message)
            message.content = ""
            message.connector_metadata = {}
            message.delivery_state = "conflicted"
            message.revision += 1
            return "conflicted"
    invalidate_sources(session, message)
    message.delivery_state = "active"
    message.content = event.text
    message.message_type = event.message_type
    message.connector_metadata = {
        **event.metadata.model_dump(exclude_none=True),
        "from_me": event.from_me,
    }
    if event.version_ms is not None:
        message.connector_metadata = {
            **message.connector_metadata,
            "edited_at": (
                datetime.fromtimestamp(event.version_ms // 1000, UTC)
                + timedelta(milliseconds=event.version_ms % 1000)
            ).isoformat(),
        }
    message.revision += 1
    state.last_version_ms = event.version_ms
    enqueue_revision(session, message, event.origin)
    return "edited"


def ingest_events(session: Session, request: EventBatch) -> DeliveryResult:
    connection = require_lease(session, request)
    accepted = duplicates = ignored = 0
    settings = get_settings()
    for delivery in request.items:
        event = delivery.event
        if connection.account_id != event.account_id:
            raise HTTPException(403, "Evento pertence a outra conta")
        payload = event.model_dump(mode="json", exclude_none=True)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        existing = session.scalar(
            select(WhatsAppEventRecord).where(WhatsAppEventRecord.event_id == event.event_id)
        )
        if existing:
            if existing.payload_hash != digest:
                raise HTTPException(409, "Reutilização de event_id com payload diferente")
            duplicates += 1
            continue
        count = (
            session.scalar(
                select(func.count())
                .select_from(WhatsAppEventRecord)
                .where(WhatsAppEventRecord.account_id == event.account_id)
            )
            or 0
        )
        if count >= settings.connector_max_events:
            raise HTTPException(507, "Limite de registros de idempotência atingido")
        chat, conversation = chat_for(session, event)
        state = session.scalar(
            select(WhatsAppMessageState).where(
                WhatsAppMessageState.chat_id == chat.id,
                WhatsAppMessageState.external_message_id == event.message_id,
                WhatsAppMessageState.from_me == event.from_me,
            )
        )
        if state is None:
            state = WhatsAppMessageState(
                chat_id=chat.id, external_message_id=event.message_id, from_me=event.from_me
            )
            session.add(state)
            session.flush()
        record = WhatsAppEventRecord(
            account_id=event.account_id,
            event_id=event.event_id,
            payload_hash=digest,
            message_state_id=state.id,
            payload=payload,
        )
        session.add(record)
        outcome = (
            apply_event(session, state, conversation, event)
            if conversation is not None
            else "ignored"
        )
        record.outcome = outcome
        if outcome != "pending":
            record.payload = {}
        if outcome == "created" and conversation is not None:
            connection.captured_messages += 1
        if outcome == "ignored":
            ignored += 1
        else:
            accepted += 1
        session.flush()
        if outcome == "created" and conversation is not None:
            pending = list(
                session.scalars(
                    select(WhatsAppEventRecord)
                    .where(
                        WhatsAppEventRecord.message_state_id == state.id,
                        WhatsAppEventRecord.outcome == "pending",
                    )
                    .order_by(WhatsAppEventRecord.created_at, WhatsAppEventRecord.id)
                )
            )
            # Native edit versions arbitrate reordering. A revoke is terminal.
            pending.sort(
                key=lambda item: (
                    item.payload.get("event_type") == "message.revoke",
                    MessageEvent.model_validate(item.payload).version_ms or 0,
                )
            )
            for old in pending:
                old.outcome = apply_event(
                    session, state, conversation, MessageEvent.model_validate(old.payload)
                )
                old.payload = {}
        connection.last_event_at = now()
    pending_count = (
        session.scalar(
            select(func.count())
            .select_from(WhatsAppEventRecord)
            .where(WhatsAppEventRecord.outcome == "pending")
        )
        or 0
    )
    if pending_count > settings.connector_max_pending:
        raise HTTPException(507, "Limite de eventos aguardando mensagem original")
    connection.duplicate_events += duplicates
    connection.ignored_events += ignored
    connection.last_sequence = max(
        connection.last_sequence, max(item.sequence for item in request.items)
    )
    connection.last_synced_at = now()
    return DeliveryResult(
        accepted=accepted,
        duplicates=duplicates,
        ignored=ignored,
        last_sequence=connection.last_sequence,
    )


def suppress_deleted_conversation(session: Session, conversation_id: UUID) -> None:
    chat_ids = select(WhatsAppChat.id).where(WhatsAppChat.conversation_id == conversation_id)
    states = list(
        session.scalars(
            select(WhatsAppMessageState).where(WhatsAppMessageState.chat_id.in_(chat_ids))
        )
    )
    for state in states:
        state.deleted_locally = True
        for event in session.scalars(
            select(WhatsAppEventRecord).where(
                WhatsAppEventRecord.message_state_id == state.id,
                WhatsAppEventRecord.outcome == "pending",
            )
        ):
            event.payload = {}
            event.outcome = "ignored"
