"""Bounded dialogue expansion; explicit query scope remains a hard boundary."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models.entities import Message


def dialogue_context(
    session: Session,
    anchors: list[Message],
    *,
    contact_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_later: bool = True,
    limit: int = 24,
    budget: int = 24000,
    neighbors: int = 2,
) -> list[Message]:
    selected: dict[UUID, Message] = {}
    size = 0

    def add(row: Message) -> None:
        nonlocal size
        if (
            row.id not in selected
            and row.delivery_state == "active"
            and row.content
            and len(row.content) <= 8000
            and size + len(row.content) <= budget
            and len(selected) < limit
        ):
            selected[row.id] = row
            size += len(row.content)

    for anchor in anchors:
        add(anchor)
    for anchor in list(selected.values()):
        scope = [
            Message.conversation_id == anchor.conversation_id,
            Message.delivery_state == "active",
            Message.content != "",
        ]
        if contact_id:
            scope.append(Message.sender_contact_id == contact_id)
        if date_from:
            scope.append(Message.sent_at >= date_from)
        if date_to:
            scope.append(Message.sent_at <= date_to)
        quoted = anchor.connector_metadata.get("quoted_message_id")
        if isinstance(quoted, str):
            parents = list(
                session.scalars(
                    select(Message).where(*scope, Message.external_message_id == quoted).limit(2)
                )
            )
            if len(parents) == 1:
                add(parents[0])
        order = tuple_(Message.sent_at, Message.sequence, Message.id)
        position = (anchor.sent_at, anchor.sequence, anchor.id)
        previous = session.scalars(
            select(Message)
            .where(*scope, order < position)
            .order_by(Message.sent_at.desc(), Message.sequence.desc(), Message.id.desc())
            .limit(neighbors)
        )
        for row in previous:
            add(row)
        if include_later:
            following = session.scalars(
                select(Message)
                .where(*scope, order > position)
                .order_by(Message.sent_at, Message.sequence, Message.id)
                .limit(neighbors)
            )
            for row in following:
                add(row)
    # Preserve conversation priority while restoring dialogue order inside it.
    conversations: dict[UUID, int] = {}
    for row in anchors:
        conversations.setdefault(row.conversation_id, len(conversations))
    return sorted(
        selected.values(),
        key=lambda row: (conversations[row.conversation_id], row.sent_at, row.sequence, row.id),
    )
