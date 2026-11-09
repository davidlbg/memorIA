import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, insert, literal, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.local import EXTRACTION_PROMPT
from app.models.entities import Conversation, ImportJob, Message, MessageWorkItem


def processing_identity(settings: Settings) -> str:
    values = {
        "pipeline": "context-v2-lineage",
        "local_decoder": "grounded-citations-v10-evidence-first"
        if settings.llm_provider == "ollama"
        else "",
        "embedding": settings.embedding_identity,
        "llm": settings.llm_provider,
        "model": settings.llm_model if settings.llm_provider != "mock" else "mock-v1",
        "digest": settings.ollama_llm_digest if settings.llm_provider == "ollama" else "",
        "local_extraction_prompt": hashlib.sha256(EXTRACTION_PROMPT.encode()).hexdigest()
        if settings.llm_provider == "ollama"
        else "",
    }
    if settings.llm_provider == "ollama":
        values["local_context_characters"] = str(settings.local_ai_context_characters)
    if settings.llm_provider == "openai":
        values["openai_decoder"] = "grounded-evidence-v3-evidence-first"
        values["openai_extraction_prompt"] = hashlib.sha256(EXTRACTION_PROMPT.encode()).hexdigest()
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def enqueue_reprocessing(session: Session, conversation_id: UUID, settings: Settings) -> ImportJob:
    conversation = session.scalar(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    if conversation is None:
        raise HTTPException(404, "Conversa não encontrada")
    identity = processing_identity(settings)
    pending = session.scalar(
        select(ImportJob).where(
            ImportJob.conversation_id == conversation_id,
            ImportJob.source_kind == "reindex",
            ImportJob.processing_identity == identity,
            ImportJob.status.in_(["pending", "processing"]),
        )
    )
    if pending:
        return pending
    conditions = [
        Message.conversation_id == conversation_id,
        Message.delivery_state == "active",
        Message.message_type == "text",
        Message.content != "",
        func.char_length(Message.content) <= 8000,
    ]
    count = session.execute(
        select(func.count()).select_from(Message).where(*conditions)
    ).scalar_one()
    if not count:
        raise HTTPException(422, "A conversa não contém mensagens elegíveis para reprocessamento")
    job = ImportJob(
        conversation_id=conversation_id,
        filename="Reprocessamento de memórias e embeddings",
        source_kind="reindex",
        processing_identity=identity,
        status="pending",
        total_messages=count,
        imported_messages=count,
        processed_messages=0,
        attempts=0,
    )
    session.add(job)
    session.flush()
    session.execute(
        insert(MessageWorkItem).from_select(
            ["id", "job_id", "message_id", "revision", "created_at"],
            select(
                func.gen_random_uuid(), literal(job.id), Message.id, Message.revision, func.now()
            ).where(*conditions),
        )
    )
    return job
