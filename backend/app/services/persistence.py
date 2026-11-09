from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.ingestion.parser import normalize
from app.models.entities import Contact, Conversation, ImportJob, Message


def ensure_conversation(
    session: Session, external_id: str, title: str, kind: str = "unknown"
) -> Conversation:
    session.execute(
        insert(Conversation)
        .values(external_id=external_id, title=title, conversation_type=kind)
        .on_conflict_do_nothing(index_elements=["external_id"])
    )
    conversation = session.scalar(
        select(Conversation).where(Conversation.external_id == external_id).with_for_update()
    )
    if conversation is None:
        raise RuntimeError("Conversation insert did not persist")
    return conversation


def ensure_contact(
    session: Session,
    conversation_id: UUID,
    name: str,
    external_id: str | None = None,
    alias: str | None = None,
) -> Contact:
    identity = "wa:" + external_id if external_id else normalize(name)
    if external_id:
        identifiers = {value for value in (external_id, alias) if value}
        matches = list(
            session.scalars(
                select(Contact).where(
                    Contact.conversation_id == conversation_id,
                    or_(
                        Contact.external_identifier.in_(identifiers),
                        Contact.external_alias.in_(identifiers),
                    ),
                )
            )
        )
        if len(matches) > 1:
            raise HTTPException(409, "Conflito de identificadores de contato; revisão necessária")
        if matches:
            matched_contact = matches[0]
            new_identifiers = identifiers - {
                matched_contact.external_identifier,
                matched_contact.external_alias,
            }
            if new_identifiers:
                if matched_contact.external_alias or len(new_identifiers) > 1:
                    raise HTTPException(409, "Alias de contato incompatível")
                matched_contact.external_alias = next(iter(new_identifiers))
            return matched_contact
    session.execute(
        insert(Contact)
        .values(
            conversation_id=conversation_id,
            display_name=name,
            normalized_name=identity,
            external_identifier=external_id,
            external_alias=alias,
        )
        .on_conflict_do_nothing(index_elements=["conversation_id", "normalized_name"])
    )
    contact = session.scalar(
        select(Contact).where(
            Contact.conversation_id == conversation_id, Contact.normalized_name == identity
        )
    )
    if contact is None:
        raise RuntimeError("Contact insert did not persist")
    return contact


def persist_message(
    session: Session,
    job: ImportJob,
    conversation_id: UUID,
    contact_id: UUID | None,
    sender: str | None,
    content: str,
    kind: str,
    sent_at: datetime,
    content_hash: str,
    sequence: int,
    *,
    external_id: str | None = None,
    origin: str = "txt",
    metadata: dict[str, object] | None = None,
) -> Message | None:
    return session.scalar(
        insert(Message)
        .values(
            conversation_id=conversation_id,
            sender_contact_id=contact_id,
            sender_name_snapshot=sender,
            content=content,
            message_type=kind,
            sent_at=sent_at,
            content_hash=content_hash,
            source_import_id=job.id,
            sequence=sequence,
            external_message_id=external_id,
            origin=origin,
            connector_metadata=metadata or {},
        )
        .on_conflict_do_nothing(index_elements=["conversation_id", "content_hash"])
        .returning(Message)
    )
