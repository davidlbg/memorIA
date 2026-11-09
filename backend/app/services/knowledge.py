import re
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.ingestion.parser import normalize
from app.core.retrieval.context import dialogue_context
from app.core.retrieval.research import research
from app.core.retrieval.search import search
from app.integrations.providers import EmbeddingProvider, LLMProvider, ProviderError
from app.integrations.research import ResearchClient
from app.models.entities import (
    Contact,
    Conversation,
    Memory,
    MemoryContext,
    MemoryEmbedding,
    MemorySource,
    Message,
)
from app.schemas.domain import (
    Answer,
    ContactContext,
    ContactView,
    ConversationDetail,
    ConversationView,
    GeneratedAnswer,
    MemoryView,
    Query,
    SourceMessage,
    SupportingQuote,
)


def sources_for(session: Session, memory_id: UUID) -> list[SourceMessage]:
    return [
        SourceMessage.model_validate(row)
        for row in session.scalars(
            select(Message)
            .join(MemorySource, MemorySource.message_id == Message.id)
            .where(MemorySource.memory_id == memory_id, Message.delivery_state == "active")
            .order_by(Message.sent_at)
        )
    ]


def memory_view(session: Session, memory: Memory) -> MemoryView:
    values = {
        key: getattr(memory, key)
        for key in MemoryView.model_fields
        if key not in {"sources", "context_sources", "supporting_quotes"}
    }
    quotes = [
        SupportingQuote(message_id=row.message_id, quote=row.evidence_quote)
        for row in session.scalars(select(MemorySource).where(MemorySource.memory_id == memory.id))
        if row.evidence_quote
    ]
    sources = sources_for(session, memory.id)
    cited = {source.id for source in sources}
    context = [
        SourceMessage.model_validate(row).model_copy(update={"retrieval_role": "context"})
        for row in session.scalars(
            select(Message)
            .join(MemoryContext, MemoryContext.message_id == Message.id)
            .where(
                MemoryContext.memory_id == memory.id,
                Message.delivery_state == "active",
                Message.revision == MemoryContext.revision,
                Message.id.not_in(cited),
            )
            .order_by(Message.sent_at, Message.sequence, Message.id)
        )
    ]
    return MemoryView.model_validate(
        {**values, "sources": sources, "context_sources": context, "supporting_quotes": quotes}
    )


def conversation_detail(
    session: Session,
    conversation_id: UUID,
    include_messages: bool = True,
    limit: int = 50,
    offset: int = 0,
    order: Literal["asc", "desc"] = "asc",
    before_id: UUID | None = None,
) -> ConversationDetail:
    row = session.get(Conversation, conversation_id)
    if row is None:
        raise HTTPException(404, "Conversa não encontrada")
    scope = [Message.conversation_id == conversation_id, Message.delivery_state == "active"]
    if before_id:
        cursor = session.get(Message, before_id)
        if cursor is None or cursor.conversation_id != conversation_id:
            raise HTTPException(404, "Cursor não pertence a esta conversa")
        scope.append(
            tuple_(Message.sent_at, Message.sequence, Message.id)
            < (cursor.sent_at, cursor.sequence, cursor.id)
        )
    ordering = (
        (Message.sent_at.desc(), Message.sequence.desc(), Message.id.desc())
        if order == "desc"
        else (Message.sent_at, Message.sequence, Message.id)
    )
    return ConversationDetail.model_validate(
        {
            "conversation": ConversationView.model_validate(row),
            "messages": [
                SourceMessage.model_validate(m)
                for m in session.scalars(
                    select(Message)
                    .where(
                        *scope,
                    )
                    .order_by(*ordering)
                    .offset(offset)
                    .limit(limit)
                )
            ]
            if include_messages
            else [],
        }
    )


async def contact_context(
    session: Session,
    settings: Settings,
    embeddings: EmbeddingProvider,
    contact_id: UUID,
    query: str = "",
) -> ContactContext:
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(404, "Contato não encontrado")
    ids = await search(
        session, settings, embeddings, kind="memories", query=query, contact_id=contact_id, limit=20
    )
    memories = [session.get(Memory, item) for item in ids]
    return ContactContext.model_validate(
        {
            "contact": ContactView.model_validate(contact),
            "memories": [memory_view(session, memory) for memory in memories if memory is not None],
        }
    )


async def answer_query(
    session: Session,
    settings: Settings,
    llm: LLMProvider,
    embeddings: EmbeddingProvider,
    query: Query,
) -> Answer:
    if isinstance(llm, ResearchClient):
        result = await research(session, settings, query, llm, embeddings)
        if result.generated is not None:
            answer = validate_answer(session, settings, result.generated, result.selected, [], result.inspected, compact_context=query.compact_context)
        elif not result.selected or result.clarification:
            # Revalidate every inspected original even when research ends without claims.
            answer = validate_answer(session, settings, GeneratedAnswer(claims=[]), [], [], result.inspected, compact_context=query.compact_context)
            answer.clarification = result.clarification
        else:
            from datetime import UTC, datetime
            from zoneinfo import ZoneInfo
            clock = datetime.now(UTC).astimezone(ZoneInfo(query.timezone)).isoformat()
            prompt = "Current user question: " + query.question + "\nApplication clock (not conversation evidence): " + clock
            if query.previous_question:
                prompt += "\nPrevious user question, topic only: " + query.previous_question
            generated = await llm.generate(prompt, result.selected)
            answer = validate_answer(session, settings, generated, result.selected, [], result.inspected, compact_context=query.compact_context)
        answer.research_steps = result.steps
        answer.research_limited = result.limited
        answer.response_mode = result.response_mode
        answer.coverage = result.coverage
        return answer
    retrieval_question = query.question
    if query.previous_question:
        retrieval_question = query.previous_question + "\n" + query.question
    if query.contact_id is None:
        contacts = select(Contact)
        if query.conversation_id:
            contacts = contacts.where(Contact.conversation_id == query.conversation_id)
        question = f" {normalize(retrieval_question)} "
        matches = [
            person
            for person in session.scalars(contacts)
            if re.search(
                r"(?<!\w)" + re.escape(normalize(person.display_name)) + r"(?!\w)", question
            )
        ]
        matched_names: dict[str, int] = {}
        for person in matches:
            name = normalize(person.display_name)
            matched_names[name] = matched_names.get(name, 0) + 1
        if any(count > 1 for count in matched_names.values()):
            return Answer(
                claims=[],
                insufficient_evidence=True,
                sources=[],
                provider=settings.llm_provider,
                clarification="Há mais de um contato com esse nome. Selecione a conversa e o contato antes de perguntar.",
            )
        if len(matches) == 1 and query.conversation_id is None:
            # A name scopes retrieval to the known conversation; it never merges
            # contacts or drops the other participant's supporting replies.
            query = query.model_copy(update={"conversation_id": matches[0].conversation_id})
    ids = await search(
        session,
        settings,
        embeddings,
        kind="messages",
        query=retrieval_question,
        contact_id=query.contact_id,
        conversation_id=query.conversation_id,
        date_from=query.date_from,
        date_to=query.date_to,
        limit=12,
    )
    memory_ids = await search(
        session,
        settings,
        embeddings,
        kind="memories",
        query=retrieval_question,
        contact_id=query.contact_id,
        conversation_id=query.conversation_id,
        date_from=query.date_from,
        date_to=query.date_to,
        memory_type=query.memory_type,
        limit=6,
    )
    # Every memory contributes its original evidence, never an unsupported summary.
    for memory_id in memory_ids:
        for source in sources_for(session, memory_id):
            if query.date_from and source.sent_at < query.date_from:
                continue
            if query.date_to and source.sent_at > query.date_to:
                continue
            if query.contact_id and source.sender_contact_id != query.contact_id:
                continue
            if source.id not in ids:
                ids.append(source.id)
    anchors = [row for item in ids[:24] if (row := session.get(Message, item)) is not None]
    rows = dialogue_context(
        session,
        anchors,
        contact_id=query.contact_id,
        date_from=query.date_from,
        date_to=query.date_to,
        budget=min(24000, settings.local_ai_context_characters)
        if settings.llm_provider == "ollama"
        else 24000,
    )
    anchor_ids = {row.id for row in anchors}
    titles = {
        row.id: row.title
        for row in session.scalars(
            select(Conversation).where(Conversation.id.in_({row.conversation_id for row in rows}))
        )
    }
    context = [
        SourceMessage.model_validate(row).model_copy(
            update={
                "retrieval_role": "match" if row.id in anchor_ids else "context",
                "conversation_title": titles[row.conversation_id],
            }
        )
        for row in rows
    ]
    if not context:
        return Answer(
            claims=[], insufficient_evidence=True, sources=[], provider=settings.llm_provider
        )
    prompt = query.question
    if query.previous_question:
        prompt = (
            "Current question: "
            + query.question
            + "\nPrevious user question, for resolving the topic only (not evidence): "
            + query.previous_question
        )
    generated = await llm.generate(prompt, context)
    return validate_answer(session, settings, generated, context, memory_ids)


def validate_answer(session: Session, settings: Settings, generated: GeneratedAnswer, context: list[SourceMessage], memory_ids: list[UUID], inspected: list[SourceMessage] | None = None, *, compact_context: bool = False) -> Answer:
    lookup = {source.id: source for source in context}
    for claim in generated.claims:
        if any(item not in lookup for item in claim.source_message_ids):
            raise ProviderError("Answer cites evidence outside retrieved context")
        if not any(claim.quote in lookup[item].content for item in claim.source_message_ids):
            raise ProviderError("Answer has no verbatim supporting evidence")
        if claim.supporting_quotes and (set(quote.message_id for quote in claim.supporting_quotes) != set(claim.source_message_ids) or any(quote.message_id not in lookup or quote.quote not in lookup[quote.message_id].content for quote in claim.supporting_quotes)):
            raise ProviderError("Answer has invalid per-message supporting quotes")
        if claim.kind == "explicit_statement" and claim.text != claim.quote:
            raise ProviderError("An explicit statement must be an original quotation")
        if claim.kind == "extracted_memory":
            allowed = [session.get(Memory, item) for item in memory_ids]
            if not any(
                memory
                and memory.evidence_basis == "explicit"
                and memory.content == claim.text
                and set(claim.source_message_ids).issubset(
                    {source.id for source in sources_for(session, memory.id)}
                )
                for memory in allowed
            ):
                raise ProviderError("Answer cites an unsupported extracted memory")
    cited = {item for claim in generated.claims for item in claim.source_message_ids}
    # Revalidate after the potentially long LLM call. Shared row locks remain
    # until this request transaction commits, preventing an edit/revoke from
    # turning the returned evidence into a stale quotation during validation.
    validation_sources = inspected if inspected is not None else context
    validation_lookup = {source.id: source for source in validation_sources}
    current = {
        row.id: row
        for row in session.scalars(
            select(Message)
            .where(Message.id.in_(validation_lookup))
            .order_by(Message.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    }
    if any(
        source.id not in current
        or current[source.id].delivery_state != "active"
        or current[source.id].revision != source.revision
        or current[source.id].content != source.content
        for source in validation_sources
    ):
        raise ProviderError("Evidence changed during the query; retry against current history")
    from app.schemas.domain import SourceRevision
    visible_context = context if compact_context else validation_sources
    visible_ids = cited | {source.id for source in visible_context}
    return Answer(
        claims=generated.claims,
        insufficient_evidence=not generated.claims,
        sources=[source for source in context if source.id in cited],
        context_sources=[source for source in visible_context if source.id not in cited],
        context_revisions=[SourceRevision(id=source.id, revision=source.revision) for source in validation_sources if source.id not in visible_ids] if compact_context else [],
        provider=settings.llm_provider,
    )


def delete_memory(session: Session, memory_id: UUID) -> None:
    memory = session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(404, "Memória não encontrada")
    # Tombstones prevent regeneration from old messages; scrub derived text.
    memory.status = "deleted"
    memory.content = ""
    memory.normalized_content = ""
    memory.evidence_summary = ""
    session.execute(delete(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory_id))
    session.execute(
        update(MemorySource).where(MemorySource.memory_id == memory_id).values(evidence_quote="")
    )
