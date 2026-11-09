import hashlib
import json
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.ingestion.parser import normalize
from app.core.retrieval.context import dialogue_context
from app.integrations.providers import (
    EmbeddingProvider,
    LLMProvider,
    ProviderError,
    validate_vectors,
)
from app.models.entities import (
    Conversation,
    Memory,
    MemoryContext,
    MemoryEmbedding,
    MemorySource,
    Message,
    MessageEmbedding,
)
from app.schemas.domain import ExtractedMemory, SourceMessage


def validate_memory(item: ExtractedMemory, messages: dict[UUID, Message]) -> list[Message]:
    if len(set(item.source_message_ids)) != len(item.source_message_ids):
        raise ProviderError("Duplicate memory source references")
    if any(source not in messages for source in item.source_message_ids):
        raise ProviderError("Unsupported memory source reference")
    sources = [messages[source] for source in item.source_message_ids]
    if len({source.conversation_id for source in sources}) != 1:
        raise ProviderError("Cross-conversation memory rejected")
    if item.evidence_basis == "explicit":
        if item.content != item.evidence_quote or not all(
            item.evidence_quote in source.content for source in sources
        ):
            raise ProviderError("Memory has no verbatim evidence")
    elif not item.supporting_quotes:
        raise ProviderError("Interpreted memory requires per-message evidence")
    if item.supporting_quotes:
        quotes = {quote.message_id: quote.quote for quote in item.supporting_quotes}
        if (
            len(quotes) != len(item.supporting_quotes)
            or set(quotes) != set(item.source_message_ids)
            or any(quotes[source.id] not in source.content for source in sources)
            or item.evidence_quote not in quotes.values()
        ):
            raise ProviderError("Memory support is incomplete or not verbatim")
    if item.evidence_basis == "interpretation" and item.observed_at != max(
        source.content_updated_at or source.sent_at for source in sources
    ):
        raise ProviderError("Contextual memory must use the most recent supporting observation")
    if not any(
        item.observed_at == (source.content_updated_at or source.sent_at) for source in sources
    ):
        raise ProviderError("Unsupported memory timestamp")
    if item.subject is not None and not all(
        item.subject == source.sender_name_snapshot for source in sources
    ):
        raise ProviderError("Unsupported subject")
    if (
        item.evidence_basis == "explicit"
        and item.memory_type in ("fact", "event")
        and item.confidence < 0.7
    ):
        raise ProviderError("Uncertain statement cannot be persisted as confirmed information")
    if (
        item.evidence_basis == "explicit"
        and item.memory_type in ("fact", "event")
        and re.search(r"\?|\b(talvez|maybe|hypothetically|hipoteticamente)\b", item.content, re.I)
    ):
        raise ProviderError("Question or uncertain evidence cannot become a confirmed memory")
    return sources


def consolidate(session: Session, memory: Memory) -> None:
    """Only a narrow explicit cancellation grammar is automatic in v0.1.

    Other potential contradictions are preserved independently, not overwritten.
    The new literal cancellation remains a source-backed historical memory.
    """
    if (
        memory.evidence_basis != "explicit"
        or memory.memory_type != "intention"
        or memory.subject_contact_id is None
    ):
        return
    candidates = session.scalars(
        select(Memory).where(
            Memory.conversation_id == memory.conversation_id,
            Memory.subject_contact_id == memory.subject_contact_id,
            Memory.memory_type == "intention",
            Memory.status == "active",
            Memory.evidence_basis == "explicit",
        )
    ).all()
    for cancellation_memory in candidates:
        cancellation = re.match(
            r"^(?:eu )?desisti de (.+?)[.!]?$|^i no longer plan to (.+?)[.!]?$",
            cancellation_memory.normalized_content,
        )
        if not cancellation:
            continue
        topic = next(group for group in cancellation.groups() if group)
        for previous in candidates:
            intention = re.match(
                r"^(?:eu )?(?:pretendo|planejo) (.+?)[.!]?$|^i (?:plan to|intend to) (.+?)[.!]?$",
                previous.normalized_content,
            )
            if (
                intention
                and previous.observed_at < cancellation_memory.observed_at
                and next(group for group in intention.groups() if group) == topic
            ):
                previous.status = "outdated"


async def process_batch(
    session: Session,
    messages: list[Message],
    settings: Settings,
    llm: LLMProvider,
    embeddings: EmbeddingProvider,
) -> None:
    # Full content remains stored. Bounded processing rejects oversized messages
    # from AI rather than truncating evidence silently.
    eligible = [
        message
        for message in messages
        if message.message_type == "text"
        and message.content.strip()
        and message.delivery_state == "active"
        and len(message.content) <= 8000
    ]
    if not eligible:
        return
    context = dialogue_context(
        session,
        eligible,
        include_later=False,
        limit=24,
        budget=settings.local_ai_context_characters if settings.llm_provider == "ollama" else 32000,
    )
    current_ids = {message.id for message in eligible}
    # Mock extraction is intentionally limited to the current batch; real models
    # may relate preceding dialogue, with at least one current supporting message.
    extraction_context = eligible if settings.llm_provider == "mock" else context
    snapshots = [
        SourceMessage.model_validate(m).model_copy(
            update={"retrieval_role": "match" if m.id in current_ids else "context"}
        )
        for m in extraction_context
    ]
    vectors = await embeddings.embed_texts([m.content for m in eligible]) if settings.worker_indexes_messages else []
    if settings.worker_indexes_messages:
        validate_vectors(vectors, len(eligible), settings.embedding_dimensions)
    extracted = await llm.extract_memories(snapshots)
    lookup = {m.id: m for m in extraction_context}
    supported = []
    for item in extracted.memories:
        sources = validate_memory(item, lookup)
        if not current_ids.intersection(item.source_message_ids):
            continue
        supported.append((item, sources))
    memory_vectors = (
        await embeddings.embed_texts([item.content for item, _ in supported]) if supported else []
    )
    validate_vectors(memory_vectors, len(supported), settings.embedding_dimensions)
    # Inference holds no row locks or uncommitted derived writes. Capture and
    # edits can proceed; serialize only the final validation and persistence.
    conversation_ids = {m.conversation_id for m in extraction_context}
    locked = list(
        session.scalars(
            select(Conversation.id)
            .where(Conversation.id.in_(conversation_ids))
            .order_by(Conversation.id)
            .with_for_update()
        )
    )
    current = {
        m.id: m
        for m in session.scalars(
            select(Message)
            .where(Message.id.in_(lookup))
            .order_by(Message.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    }
    if len(locked) != len(conversation_ids) or any(
        snapshot.id not in current
        or current[snapshot.id].delivery_state != "active"
        or current[snapshot.id].revision != snapshot.revision
        or current[snapshot.id].content != snapshot.content
        for snapshot in snapshots
    ):
        raise ProviderError("Extraction inputs changed during inference; retry current revisions")
    for message, vector in zip(eligible if settings.worker_indexes_messages else [], vectors, strict=True):
        session.execute(
            insert(MessageEmbedding)
            .values(message_id=message.id, embedding=vector, model_name=settings.embedding_identity)
            .on_conflict_do_nothing(index_elements=["message_id", "model_name"])
        )
    for (item, sources), memory_vector in zip(supported, memory_vectors, strict=True):
        quotes = {quote.message_id: quote.quote for quote in item.supporting_quotes}
        if not quotes:
            quotes = {source.id: item.evidence_quote for source in sources}
        subject_contacts = {
            source.sender_contact_id
            for source in sources
            if item.subject and source.sender_name_snapshot == item.subject
        }
        # Display-name collisions leave contact identity explicitly unknown.
        subject_id = next(iter(subject_contacts)) if len(subject_contacts) == 1 else None
        canonical = normalize(item.content)
        # Repeated events/plans at different times are distinct episodes.
        episode = "" if item.memory_type == "fact" else item.observed_at.isoformat()
        identity = f"{item.memory_type}:{subject_id}:{canonical}:{episode}"
        if item.evidence_basis == "interpretation":
            support = sorted((str(source.id), quotes[source.id]) for source in sources)
            identity = f"interpretation:{item.memory_type}:{subject_id}:" + json.dumps(
                support, ensure_ascii=False
            )
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        memory = session.scalar(
            select(Memory).where(
                Memory.conversation_id == sources[0].conversation_id,
                Memory.fingerprint == fingerprint,
            )
        )
        if memory is None:
            memory = Memory(
                conversation_id=sources[0].conversation_id,
                memory_type=item.memory_type,
                subject_contact_id=subject_id,
                content=item.content,
                normalized_content=canonical,
                fingerprint=fingerprint,
                confidence=item.confidence,
                status="active",
                observed_at=item.observed_at,
                evidence_summary=item.evidence_summary,
                evidence_basis=item.evidence_basis,
            )
            session.add(memory)
            session.flush()
        elif memory.status == "deleted":
            # A user deletion is a persistent tombstone until conversation deletion.
            continue
        for source in sources:
            session.execute(
                insert(MemorySource)
                .values(
                    memory_id=memory.id,
                    message_id=source.id,
                    relevance_score=1,
                    evidence_quote=quotes[source.id],
                )
                .on_conflict_do_nothing(index_elements=["memory_id", "message_id"])
            )
        if settings.llm_provider != "mock":
            for dependency in extraction_context:
                session.execute(
                    insert(MemoryContext)
                    .values(
                        memory_id=memory.id, message_id=dependency.id, revision=dependency.revision
                    )
                    .on_conflict_do_nothing(index_elements=["memory_id", "message_id"])
                )
        exists = session.scalar(
            select(MemoryEmbedding.id).where(
                MemoryEmbedding.memory_id == memory.id,
                MemoryEmbedding.model_name == settings.embedding_identity,
            )
        )
        if not exists:
            session.add(
                MemoryEmbedding(
                    memory_id=memory.id,
                    embedding=memory_vector,
                    model_name=settings.embedding_identity,
                )
            )
        session.flush()
        consolidate(session, memory)
