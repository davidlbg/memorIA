from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import database
from app.config import get_settings
from app.core.ingestion.parser import DateOrder, ParseError, Preview, parse_export
from app.core.retrieval.search import search
from app.integrations.providers import OpenAIEmbeddingProvider, OpenAIProvider, providers
from app.models.entities import Contact, Conversation, ImportJob, Memory, Message
from app.schemas.domain import (
    Answer,
    ContactContext,
    ContactView,
    ConversationDetail,
    ConversationView,
    Deleted,
    HealthView,
    ImportView,
    MemoryType,
    MemoryView,
    OpenAIConfigurationRequest,
    ProviderSelection,
    SourceMessage,
    SourceRevision,
    SourceValidation,
)
from app.schemas.domain import Query as AssistantQuery
from app.schemas.usage import BudgetStatus
from app.security.ai_credentials import cipher
from app.security.auth import authenticated
from app.services.imports import commit_import
from app.services.knowledge import (
    answer_query,
    contact_context,
    conversation_detail,
    delete_memory,
    memory_view,
)
from app.services.reprocessing import enqueue_reprocessing
from app.services.settings import remove_openai, runtime_settings, save_openai, select_provider
from app.services.usage import budget_status, operation_cost, usage_operation
from app.services.whatsapp import suppress_deleted_conversation

router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticated)])
DB = Annotated[Session, Depends(database, scope="function")]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
SearchQuery = Annotated[str, Query(max_length=2000)]


@router.post("/messages/validate", response_model=list[SourceRevision])
def validate_sources(body: SourceValidation, session: DB) -> list[SourceRevision]:
    current = dict(
        session.execute(
            select(Message.id, Message.revision).where(
                Message.id.in_({item.id for item in body.sources}),
                Message.delivery_state == "active",
            )
        )
        .tuples()
        .all()
    )
    seen: set[tuple[UUID, int]] = set()
    valid: list[SourceRevision] = []
    for item in body.sources:
        key = (item.id, item.revision)
        if current.get(item.id) == item.revision and key not in seen:
            seen.add(key)
            valid.append(item)
    return valid


async def read_upload(file: UploadFile, date_order: DateOrder, timezone: str) -> Preview:
    try:
        if not file.filename or not file.filename.lower().endswith(".txt"):
            raise HTTPException(422, "Envie uma exportação .txt do WhatsApp")
        if len(timezone) > 100:
            raise HTTPException(422, "Fuso inválido")
        limit = get_settings().import_max_bytes
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, "Arquivo excede o limite de importação")
        return parse_export(data, date_order, timezone)
    except ParseError as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        await file.close()


@router.post("/imports/preview", response_model=Preview)
async def preview_import(
    file: UploadFile = File(), date_order: DateOrder = Form(), timezone: str = Form()
) -> Preview:
    result = await read_upload(file, date_order, timezone)
    return result.model_copy(update={"messages": result.messages[:100]})


@router.post("/imports", response_model=ImportView, status_code=201)
async def import_file(
    session: DB,
    file: UploadFile = File(),
    date_order: DateOrder = Form(),
    timezone: str = Form(),
    title: str = Form(min_length=1, max_length=200),
    conversation_id: UUID | None = Form(default=None),
    preview_hash: str = Form(min_length=64, max_length=64),
) -> ImportView:
    if not title.strip():
        raise HTTPException(422, "Informe um título")
    preview = await read_upload(file, date_order, timezone)
    if preview_hash != preview.file_hash:
        raise HTTPException(409, "Arquivo diferente da prévia; revise novamente")
    job = await run_in_threadpool(
        commit_import,
        session,
        preview,
        file.filename or "export.txt",
        title.strip(),
        conversation_id,
    )
    return ImportView.model_validate(job)


@router.get("/imports", response_model=list[ImportView])
def imports(session: DB, limit: Limit = 30, offset: Offset = 0) -> list[ImportJob]:
    return list(
        session.scalars(
            select(ImportJob)
            .order_by(ImportJob.created_at.desc(), ImportJob.id)
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/imports/{job_id}", response_model=ImportView)
def get_import(job_id: UUID, session: DB) -> ImportJob:
    job = session.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(404, "Importação não encontrada")
    return job


@router.post("/imports/{job_id}/retry", response_model=ImportView)
def retry_import(job_id: UUID, session: DB) -> ImportJob:
    job = get_import(job_id, session)
    session.scalar(
        select(Conversation).where(Conversation.id == job.conversation_id).with_for_update()
    )
    session.refresh(job)
    if job.status != "failed":
        raise HTTPException(409, "Somente importações com falha podem ser retomadas")
    job.status, job.attempts, job.error_message = "pending", 0, None
    return job


@router.get("/conversations", response_model=list[ConversationView])
def conversations(session: DB, limit: Limit = 30, offset: Offset = 0) -> list[Conversation]:
    return list(
        session.scalars(
            select(Conversation)
            .order_by(Conversation.imported_at.desc(), Conversation.id)
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    session: DB,
    limit: Limit = 50,
    offset: Offset = 0,
    order: Literal["asc", "desc"] = "asc",
    before_id: UUID | None = None,
) -> ConversationDetail:
    return conversation_detail(session, conversation_id, True, limit, offset, order, before_id)


@router.post(
    "/conversations/{conversation_id}/reprocess", response_model=ImportView, status_code=202
)
def reprocess_conversation(conversation_id: UUID, session: DB) -> ImportJob:
    return enqueue_reprocessing(session, conversation_id, runtime_settings(session))


@router.delete("/conversations/{conversation_id}", response_model=Deleted)
def remove_conversation(conversation_id: UUID, session: DB) -> Deleted:
    row = session.scalar(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Conversa não encontrada")
    suppress_deleted_conversation(session, conversation_id)
    session.execute(delete(Conversation).where(Conversation.id == conversation_id))
    return Deleted()


@router.get("/messages/search", response_model=list[SourceMessage])
async def search_messages(
    session: DB,
    query: SearchQuery = "",
    contact_id: UUID | None = None,
    conversation_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Limit = 20,
) -> list[SourceMessage]:
    validate_dates(date_from, date_to)
    settings = runtime_settings(session)
    _, embeddings = providers(settings)
    ids = await search(
        session,
        settings,
        embeddings,
        kind="messages",
        query=query,
        contact_id=contact_id,
        conversation_id=conversation_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return [
        SourceMessage.model_validate(row)
        for item in ids
        if (row := session.get(Message, item)) is not None
    ]


@router.get("/memories", response_model=list[MemoryView])
def memories(
    session: DB,
    memory_type: MemoryType | None = None,
    contact_id: UUID | None = None,
    conversation_id: UUID | None = None,
    limit: Limit = 30,
    offset: Offset = 0,
) -> list[MemoryView]:
    stmt = select(Memory).where(Memory.status != "deleted")
    if memory_type:
        stmt = stmt.where(Memory.memory_type == memory_type)
    if contact_id:
        stmt = stmt.where(Memory.subject_contact_id == contact_id)
    if conversation_id:
        stmt = stmt.where(Memory.conversation_id == conversation_id)
    return [
        memory_view(session, row)
        for row in session.scalars(
            stmt.order_by(Memory.observed_at.desc(), Memory.id).offset(offset).limit(limit)
        )
    ]


@router.get("/memories/search", response_model=list[MemoryView])
async def search_memories(
    session: DB,
    query: SearchQuery = "",
    memory_type: MemoryType | None = None,
    contact_id: UUID | None = None,
    conversation_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Limit = 20,
) -> list[MemoryView]:
    validate_dates(date_from, date_to)
    settings = runtime_settings(session)
    _, embeddings = providers(settings)
    ids = await search(
        session,
        settings,
        embeddings,
        kind="memories",
        query=query,
        contact_id=contact_id,
        conversation_id=conversation_id,
        date_from=date_from,
        date_to=date_to,
        memory_type=memory_type,
        limit=limit,
    )
    return [
        memory_view(session, row) for item in ids if (row := session.get(Memory, item)) is not None
    ]


@router.get("/memories/{memory_id}", response_model=MemoryView)
def get_memory(memory_id: UUID, session: DB) -> MemoryView:
    row = session.get(Memory, memory_id)
    if row is None or row.status == "deleted":
        raise HTTPException(404, "Memória não encontrada")
    return memory_view(session, row)


@router.delete("/memories/{memory_id}", response_model=Deleted)
def remove_memory(memory_id: UUID, session: DB) -> Deleted:
    # Serialize with the processing pipeline to prevent concurrent regeneration.
    memory = session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(404, "Memória não encontrada")
    session.scalar(
        select(Conversation).where(Conversation.id == memory.conversation_id).with_for_update()
    )
    session.refresh(memory)
    delete_memory(session, memory_id)
    return Deleted()


@router.get("/contacts", response_model=list[ContactView])
def contacts(session: DB, limit: Limit = 100, offset: Offset = 0) -> list[Contact]:
    return list(
        session.scalars(
            select(Contact).order_by(Contact.display_name, Contact.id).offset(offset).limit(limit)
        )
    )


@router.get("/contacts/{contact_id}", response_model=ContactView)
def get_contact(contact_id: UUID, session: DB) -> Contact:
    row = session.get(Contact, contact_id)
    if row is None:
        raise HTTPException(404, "Contato não encontrado")
    return row


@router.get("/contacts/{contact_id}/context")
async def get_context(contact_id: UUID, session: DB, query: SearchQuery = "") -> ContactContext:
    settings = runtime_settings(session)
    _, embeddings = providers(settings)
    return await contact_context(session, settings, embeddings, contact_id, query)


def validate_dates(date_from: datetime | None, date_to: datetime | None) -> None:
    if any(date and date.tzinfo is None for date in (date_from, date_to)):
        raise HTTPException(422, "Datas devem incluir fuso horário")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "Intervalo de datas inválido")


@router.post("/assistant/query", response_model=Answer)
async def query_assistant(query: AssistantQuery, session: DB) -> Answer:
    validate_dates(query.date_from, query.date_to)
    settings = runtime_settings(session)
    llm, embeddings = providers(settings, for_query=True)
    with usage_operation() as identifier:
        answer = await answer_query(session, settings, llm, embeddings, query)
        if settings.ai_monthly_budget_usd is not None:
            answer.cost = operation_cost(session, identifier)
        return answer


@router.get("/settings/usage", response_model=BudgetStatus)
def get_ai_usage(session: DB) -> BudgetStatus:
    # Only local metadata. This endpoint never calls a paid provider.
    return budget_status(session, get_settings())


@router.get("/health")
def health(session: DB) -> HealthView:
    session.execute(text("SELECT 1"))
    settings = runtime_settings(session)
    return HealthView.model_validate(
        {
            "status": "ok",
            "llm_provider": settings.llm_provider,
            "embedding_provider": settings.embedding_provider,
            "embedding_identity": settings.embedding_identity,
            "external_ai_enabled": settings.external_ai_enabled,
            "import_max_bytes": settings.import_max_bytes,
            "ollama_available": settings.local_ai_enabled and bool(settings.ollama_llm_digest),
            "llm_model": settings.llm_model,
            "query_model": settings.openai_query_model if settings.llm_provider == "openai" and settings.openai_query_model else settings.llm_model,
            "local_ai_enabled": settings.local_ai_enabled,
            "openai_setup_available": bool(settings.ai_credentials_key_file) and Path(settings.ai_credentials_key_file).is_file(),
            "ai_processing_since": settings.ai_processing_since,
            "openai_available": settings.external_ai_enabled
            and bool(settings.openai_api_key.get_secret_value()),
        }
    )


@router.patch("/settings/provider", response_model=ProviderSelection)
def update_provider(body: ProviderSelection, session: DB) -> ProviderSelection:
    select_provider(session, body.llm_provider)
    return body


@router.post("/settings/openai", response_model=HealthView)
async def configure_openai(body: OpenAIConfigurationRequest, session: DB) -> HealthView:
    # Validate only public fixture text before activating or replacing a key.
    cipher(get_settings())
    values = get_settings().model_dump()
    values.update(
        llm_provider="openai", embedding_provider="openai", local_ai_enabled=False,
        external_ai_enabled=True, openai_api_key=body.api_key,
        llm_model="gpt-4.1-mini", embedding_model="text-embedding-3-small",
        ai_usage_role="setup",
    )
    from app.config import Settings
    settings = Settings.model_validate(values)
    await OpenAIEmbeddingProvider(settings).embed_texts(["Teste de conexão do WhatsApp Memory."])
    await OpenAIProvider(settings).generate("Qual é o valor do contrato?", [])
    save_openai(session, body.api_key)
    return health(session)


@router.delete("/settings/openai", response_model=HealthView)
def delete_openai(session: DB) -> HealthView:
    remove_openai(session)
    session.flush()
    return health(session)
