"""Run inside the personal backend via stdin. Emit counts only, never private text.

Requires the already authorized local providers. This does not send WhatsApp
messages, call external AI, or persist a question/answer transcript.
"""

import asyncio
import json
import time
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.database.session import session_factory
from app.integrations.providers import ProviderError, providers
from app.models.entities import ImportJob, Memory, Message, MessageEmbedding
from app.schemas.domain import Query
from app.services.knowledge import answer_query
from app.services.settings import runtime_settings


class LocalFlowError(RuntimeError):
    pass


async def main() -> None:
    started = time.monotonic()
    with session_factory()() as session:
        settings = runtime_settings(session)
        if (
            settings.llm_provider != "ollama"
            or settings.embedding_provider != "ollama"
            or not settings.local_ai_enabled
            or settings.external_ai_enabled
        ):
            raise LocalFlowError("Local-only configuration required")
        source = session.scalar(
            select(Message)
            .join(MessageEmbedding, MessageEmbedding.message_id == Message.id)
            .where(
                MessageEmbedding.model_name == settings.embedding_identity,
                Message.delivery_state == "active",
                Message.message_type == "text",
                func.char_length(Message.content).between(50, 2000),
            )
            .order_by(Message.sent_at.desc(), Message.id)
            .limit(1)
        )
        if source is None:
            raise LocalFlowError("No eligible indexed original yet")
        llm, embeddings = providers(settings, for_query=True)
        # The excerpt never leaves this container/local Ollama network.
        question = (
            "Explique o que foi dito sobre este trecho, sem inventar informações: "
            + source.content[:500]
        )
        answer = await answer_query(
            session,
            settings,
            llm,
            embeddings,
            Query(question=question, conversation_id=source.conversation_id),
        )
        if answer.insufficient_evidence or not answer.claims or not answer.sources:
            raise LocalFlowError("The personal query did not return supported claims")
        for evidence in answer.sources + answer.context_sources:
            original = session.get(Message, evidence.id)
            if (
                original is None
                or original.content != evidence.content
                or original.revision != evidence.revision
            ):
                raise LocalFlowError("Personal evidence is no longer current")
        result = {
            "status": "passed",
            "checked_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "context_budget_characters": settings.local_ai_context_characters,
            "provider": answer.provider,
            "model": settings.llm_model,
            "model_digest": settings.ollama_llm_digest,
            "embedding_identity": settings.embedding_identity,
            "claims": len(answer.claims),
            "original_sources": len(answer.sources),
            "context_inputs": len(answer.context_sources),
            "indexed_messages": session.scalar(
                select(func.count())
                .select_from(MessageEmbedding)
                .where(MessageEmbedding.model_name == settings.embedding_identity)
            ),
            "stored_memories": session.scalar(select(func.count()).select_from(Memory)),
            "completed_jobs": session.scalar(
                select(func.count())
                .select_from(ImportJob)
                .where(ImportJob.status == "completed")
            ),
        }
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ProviderError, LocalFlowError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        raise SystemExit(1) from None
    except Exception as exc:  # noqa: BLE001 — terminal boundary must suppress private exception payloads
        # Private provider/SQL payloads must never reach terminal or chat.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "category": type(exc).__name__,
                    "reason": "Unexpected local flow failure; private payload withheld",
                }
            )
        )
        raise SystemExit(1) from None
