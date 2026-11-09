import asyncio

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core.ingestion.parser import parse_export
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider
from app.schemas.domain import Query
from app.services.imports import commit_import
from app.services.knowledge import answer_query
from app.workers.runner import run_once

pytestmark = pytest.mark.database
TEXT = "03/11/2026, 10:00 - Alice: Eu trabalho como engenheira.\n03/11/2026, 10:01 - Bob: Eu moro em Recife."


async def test_question_naming_two_distinct_participants_is_not_treated_as_duplicate_identity(
    db: sessionmaker[Session],
) -> None:
    with db.begin() as session:
        conversation = commit_import(
            session, parse_export(TEXT.encode(), "DMY", "UTC"), "fixture.txt", "Synthetic", None
        ).conversation_id
    await asyncio.to_thread(
        run_once, db, get_settings(), MockLLMProvider(), MockEmbeddingProvider(256)
    )
    with db() as session:
        answer = await answer_query(
            session,
            get_settings(),
            MockLLMProvider(),
            MockEmbeddingProvider(256),
            Query(question="Entre Alice e Bob, quem mora em Recife?", conversation_id=conversation),
        )
        assert answer.clarification is None and not answer.insufficient_evidence
        assert any(source.sender_name_snapshot == "Bob" for source in answer.sources)


async def test_duplicate_name_across_conversations_requires_selection(
    db: sessionmaker[Session],
) -> None:
    with db.begin() as session:
        for index, text in enumerate([TEXT, TEXT.replace("engenheira", "professora")]):
            commit_import(
                session,
                parse_export(text.encode(), "DMY", "UTC"),
                "fixture.txt",
                f"Synthetic {index}",
                None,
            )
    with db() as session:
        answer = await answer_query(
            session,
            get_settings(),
            MockLLMProvider(),
            MockEmbeddingProvider(256),
            Query(question="O que Alice disse?"),
        )
        assert answer.insufficient_evidence and answer.clarification and not answer.sources
