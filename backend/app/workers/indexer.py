"""Index original messages independently of slow memory extraction."""

import asyncio
import logging
import time

from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database.session import session_factory
from app.integrations.errors import BudgetExceeded
from app.integrations.providers import EmbeddingProvider, providers, validate_vectors
from app.models.entities import Conversation, ImportJob, Message, MessageEmbedding, MessageWorkItem
from app.schemas.domain import SourceMessage
from app.services.settings import runtime_settings

logger = logging.getLogger("memory.indexer")


async def index_once(
    factory: sessionmaker[Session],
    settings: Settings,
    embeddings: EmbeddingProvider,
) -> int:
    if not settings.ai_background_enabled:
        return 0
    # Keep local background encoding bounded so foreground generation can progress.
    batch_limit = 4 if settings.embedding_provider == "ollama" else 16
    char_limit = 8000 if settings.embedding_provider == "ollama" else 32000
    with factory() as session:
        scope = []
        if settings.ai_processing_since is not None:
            scope.append(or_(
                exists(select(MessageWorkItem.id).join(ImportJob, ImportJob.id == MessageWorkItem.job_id).where(
                    MessageWorkItem.message_id == Message.id,
                    MessageWorkItem.revision == Message.revision,
                    ImportJob.created_at >= settings.ai_processing_since,
                    ImportJob.source_kind != "whatsapp_history",
                )),
                exists(select(ImportJob.id).where(
                    ImportJob.id == Message.source_import_id,
                    ImportJob.created_at >= settings.ai_processing_since,
                    ImportJob.source_kind == "txt",
                )),
            ))
        rows = session.scalars(
            select(Message)
            .where(
                *scope,
                Message.delivery_state == "active",
                Message.message_type == "text",
                Message.content != "",
                func.char_length(Message.content) <= 8000,
                ~exists(
                    select(MessageEmbedding.id).where(
                        MessageEmbedding.message_id == Message.id,
                        MessageEmbedding.model_name == settings.embedding_identity,
                    )
                ),
            )
            .order_by(Message.sent_at.desc(), Message.id)
            .limit(batch_limit)
        )
        snapshots: list[SourceMessage] = []
        chars = 0
        for row in rows:
            if snapshots and (
                len(snapshots) >= batch_limit or chars + len(row.content) > char_limit
            ):
                break
            snapshots.append(SourceMessage.model_validate(row))
            chars += len(row.content)
    if not snapshots:
        return 0
    vectors = await embeddings.embed_texts([row.content for row in snapshots])
    validate_vectors(vectors, len(snapshots), settings.embedding_dimensions)
    indexed = 0
    with factory.begin() as session:
        # Same lock order as capture and extraction; no locks during inference.
        list(
            session.scalars(
                select(Conversation.id)
                .where(Conversation.id.in_({row.conversation_id for row in snapshots}))
                .order_by(Conversation.id)
                .with_for_update()
            )
        )
        current = {
            row.id: row
            for row in session.scalars(
                select(Message)
                .where(Message.id.in_({row.id for row in snapshots}))
                .order_by(Message.id)
                .with_for_update(read=True)
            )
        }
        for snapshot, vector in zip(snapshots, vectors, strict=True):
            current_row = current.get(snapshot.id)
            if (
                current_row is None
                or current_row.delivery_state != "active"
                or current_row.revision != snapshot.revision
                or current_row.content != snapshot.content
            ):
                logger.info(
                    "Index input changed during inference; current revision remains pending"
                )
                continue
            inserted = session.scalar(
                insert(MessageEmbedding)
                .values(
                    message_id=current_row.id,
                    embedding=vector,
                    model_name=settings.embedding_identity,
                )
                .on_conflict_do_nothing(index_elements=["message_id", "model_name"])
                .returning(MessageEmbedding.id)
            )
            indexed += int(inserted is not None)
    return indexed


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    while True:
        try:
            with session_factory()() as session:
                settings = runtime_settings(session)
            _, embeddings = providers(settings)
            count = asyncio.run(index_once(session_factory(), settings, embeddings))
            if count:
                logger.info("Indexed message batch; messages=%s", count)
            else:
                time.sleep(settings.worker_poll_seconds)
        except BudgetExceeded:
            logger.info("Indexing paused by shared AI budget; originals remain pending")
            time.sleep(60)
        except Exception:
            # Provider/SQL exceptions may contain private content. Failure is visible;
            # originals remain pending and no fallback vectors are generated.
            logger.error("Message indexing failed; originals remain pending")
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
