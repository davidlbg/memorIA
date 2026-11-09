from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.routes.v1 import validate_dates
from app.core.retrieval.search import search
from app.database.session import session_factory
from app.integrations.providers import ProviderError, providers
from app.models.entities import Memory, Message
from app.schemas.domain import ContactContext, ConversationDetail, MemoryType, SourceMessage
from app.services.knowledge import contact_context, conversation_detail, memory_view
from app.services.settings import runtime_settings

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
Limit = Annotated[int, Field(ge=1, le=100)]
QueryText = Annotated[str, Field(max_length=2000)]


@contextmanager
def read_session() -> Iterator[Session]:
    try:
        with session_factory()() as session:
            yield session
    except (SQLAlchemyError, ProviderError):
        raise ToolError(
            "Memory service unavailable; check local database/provider configuration"
        ) from None


async def search_messages(
    query: QueryText = "",
    contact_id: UUID | None = None,
    conversation_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Limit = 20,
) -> list[SourceMessage]:
    """Search original WhatsApp messages with hybrid retrieval and explicit filters."""
    validate_dates(date_from, date_to)
    with read_session() as session:
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


async def search_memories(
    query: QueryText = "",
    memory_type: MemoryType | None = None,
    contact_id: UUID | None = None,
    limit: Limit = 20,
) -> list[dict[str, object]]:
    """Search persistent memories and return their original evidence."""
    with read_session() as session:
        settings = runtime_settings(session)
        _, embeddings = providers(settings)
        ids = await search(
            session,
            settings,
            embeddings,
            kind="memories",
            query=query,
            contact_id=contact_id,
            memory_type=memory_type,
            limit=limit,
        )
        return [
            memory_view(session, row).model_dump(mode="json")
            for item in ids
            if (row := session.get(Memory, item)) is not None
        ]


def get_conversation(
    conversation_id: UUID, include_messages: bool = True, limit: Limit = 50
) -> ConversationDetail:
    """Read an imported conversation. Use REST pagination for more than 100 messages."""
    with read_session() as session:
        return conversation_detail(session, conversation_id, include_messages, limit)


async def get_contact_context(contact_id: UUID, query: QueryText = "") -> ContactContext:
    """Read source-backed context for a contact scoped to its imported conversation."""
    with read_session() as session:
        settings = runtime_settings(session)
        _, embeddings = providers(settings)
        return await contact_context(session, settings, embeddings, contact_id, query)


async def get_timeline(
    contact_id: UUID,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Limit = 50,
) -> list[SourceMessage]:
    """Read the most recent matching messages, returned in chronological order."""
    items = await search_messages("", contact_id, None, date_from, date_to, limit)
    return sorted(items, key=lambda item: (item.sent_at, str(item.id)))


def create_mcp() -> FastMCP:
    server = FastMCP(
        "WhatsApp Memory", stateless_http=True, json_response=True, streamable_http_path="/"
    )
    for handler in (
        search_messages,
        search_memories,
        get_conversation,
        get_contact_context,
        get_timeline,
    ):
        server.tool(annotations=READ_ONLY)(handler)
    return server


mcp = create_mcp()
