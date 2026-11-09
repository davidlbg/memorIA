import asyncio
import logging
import time

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import ColumnElement

from app.config import Settings, get_settings
from app.core.memory.pipeline import process_batch
from app.database.session import session_factory
from app.integrations.errors import BudgetExceeded
from app.integrations.providers import EmbeddingProvider, LLMProvider, ProviderError, providers
from app.models.entities import Conversation, ImportJob, Message, MessageWorkItem, now
from app.services.reprocessing import processing_identity
from app.services.settings import runtime_settings

logger = logging.getLogger("memory.worker")


def run_once(
    factory: sessionmaker[Session],
    settings: Settings,
    llm: LLMProvider,
    embeddings: EmbeddingProvider,
) -> bool:
    if not settings.ai_background_enabled:
        return False
    # A transaction advisory lock excludes other workers without blocking capture
    # or edits during CPU inference. A crash releases it and rolls back the batch.
    with factory.begin() as session:
        job_filters: list[ColumnElement[bool]] = [ImportJob.status.in_(["pending", "processing"])]
        if settings.ai_processing_since is not None:
            job_filters.extend([
                ImportJob.created_at >= settings.ai_processing_since,
                ImportJob.source_kind != "whatsapp_history",
            ])
        candidates = session.scalars(
            select(Conversation)
            .where(
                Conversation.id.in_(
                    select(ImportJob.conversation_id).where(*job_filters)
                )
            )
            .order_by(Conversation.created_at)
        )
        conversation = next(
            (
                candidate
                for candidate in candidates
                if session.scalar(
                    select(
                        func.pg_try_advisory_xact_lock(func.hashtextextended(str(candidate.id), 0))
                    )
                )
            ),
            None,
        )
        if conversation is None:
            return False
        job = session.scalar(
            select(ImportJob)
            .where(
                ImportJob.conversation_id == conversation.id,
                *job_filters,
            )
            .order_by(ImportJob.created_at)
            .limit(1)
        )
        if job is None:
            return False
        if job.processing_identity and job.processing_identity != processing_identity(settings):
            job.status = "failed"
            job.error_message = "A configuração de IA mudou. Solicite novo reprocessamento com a configuração atual."
            return True
        # The worker advisory lock serializes processing. Publish only progress
        # metadata in a short transaction; otherwise "processing" stays invisible
        # throughout CPU AI. No row locks span the model call.
        # A crash leaves a reclaimable processing job and rolls all AI writes
        # back. Never commit source text or derived content through this path.
        with factory.begin() as progress:
            progress.execute(
                update(ImportJob).where(ImportJob.id == job.id).values(status="processing")
            )
        session.refresh(job)
        messages = list(
            session.scalars(
                select(Message)
                .where(Message.source_import_id == job.id)
                .order_by(Message.sequence)
                .offset(job.processed_messages)
                .limit(settings.worker_batch_messages)
            )
        )
        consumed: int | None = None
        if job.source_kind != "txt":
            items = list(
                session.scalars(
                    select(MessageWorkItem)
                    .where(MessageWorkItem.job_id == job.id)
                    .order_by(MessageWorkItem.created_at, MessageWorkItem.id)
                    .offset(job.processed_messages)
                    .limit(settings.worker_batch_messages)
                )
            )
            consumed = 0
            messages = []
            item_chars = 0
            for item in items:
                row = session.get(Message, item.message_id)
                if (
                    row is not None
                    and row.revision == item.revision
                    and row.delivery_state == "active"
                ):
                    if messages and item_chars + len(row.content) > 32000:
                        break
                    messages.append(row)
                    item_chars += len(row.content)
                consumed += 1
            if not items and job.processed_messages < job.imported_messages:
                job.status = "failed"
                job.error_message = "Fila de processamento incompleta; verifique a integridade dos itens antes de retomar."
                return True
        bounded: list[Message] = []
        chars = 0
        for message in messages:
            if bounded and chars + len(message.content) > 32000:
                break
            bounded.append(message)
            chars += len(message.content)
        job.status = "processing"
        try:
            # Savepoint rolls back embeddings/memories together on provider failure.
            with session.begin_nested():
                asyncio.run(process_batch(session, bounded, settings, llm, embeddings))
        except BudgetExceeded:
            # Keep the original job resumable next month. A budget pause is
            # neither corrupt data nor one of the three processing failures.
            job.status = "pending"
            job.error_message = "Processamento aguardando orçamento mensal de IA; originais preservados."
            return False
        except Exception as exc:
            # Deliberately do not log exception payloads: provider/database errors
            # may contain conversation text. Fail visibly, retain imported data.
            # ProviderError is our safe, constant diagnostic contract; other
            # exception payloads can contain SQL parameters or private text.
            reason = str(exc) if isinstance(exc, ProviderError) else "Unexpected processing failure"
            logger.warning("Memory batch failed; job_id=%s; reason=%s", job.id, reason)
            job.attempts += 1
            job.error_message = (
                f"Processamento falhou: {reason}. Verifique a configuração e tente novamente."
            )
            job.status = "failed" if job.attempts >= 3 else "pending"
        else:
            job.processed_messages += consumed if consumed is not None else len(bounded)
            job.error_message = None
            if job.processed_messages >= job.imported_messages:
                job.status = "completed"
                job.completed_at = now()
        return True


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    while True:
        try:
            with session_factory()() as config_session:
                settings = runtime_settings(config_session)
            llm, embeddings = providers(settings)
            run_once(session_factory(), settings, llm, embeddings)
        except Exception:
            logger.error("Worker transaction unavailable; retrying without logging private data")
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
