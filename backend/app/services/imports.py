from pathlib import PurePath
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ingestion.parser import Preview
from app.models.entities import Conversation, ImportJob, now
from app.services.persistence import ensure_contact, ensure_conversation, persist_message


def commit_import(
    session: Session, preview: Preview, filename: str, title: str, conversation_id: UUID | None
) -> ImportJob:
    if conversation_id:
        conversation = session.scalar(
            select(Conversation).where(Conversation.id == conversation_id).with_for_update()
        )
        if conversation is None:
            raise HTTPException(404, "Conversa não encontrada")
    else:
        conversation = ensure_conversation(session, "file:" + preview.file_hash, title)
    conversation.updated_at = now()
    job = ImportJob(
        conversation_id=conversation.id,
        filename=PurePath(filename.replace("\\", "/")).name[:250],
        status="pending",
        total_messages=preview.total_messages,
        imported_messages=0,
        processed_messages=0,
        attempts=0,
    )
    session.add(job)
    session.flush()
    contacts: dict[str, UUID] = {}
    for name in preview.participants:
        contact = ensure_contact(session, conversation.id, name)
        contacts[name] = contact.id
    for index, message in enumerate(preview.messages):
        added = persist_message(
            session,
            job,
            conversation.id,
            contacts.get(message.sender) if message.sender else None,
            message.sender,
            message.content,
            message.message_type,
            message.sent_at,
            message.content_hash,
            index,
        )
        if added is not None:
            job.imported_messages += 1
    if not job.imported_messages:
        job.status = "completed"
        job.completed_at = now()
    session.flush()
    return job
