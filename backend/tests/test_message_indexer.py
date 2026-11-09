import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core.ingestion.parser import parse_export
from app.integrations.providers import MockEmbeddingProvider
from app.models.entities import ImportJob, Memory, Message, MessageEmbedding
from app.services.imports import commit_import
from app.services.whatsapp import invalidate_sources
from app.workers.indexer import index_once

pytestmark = pytest.mark.database


def ingest(db: sessionmaker[Session]) -> None:
    with db.begin() as session:
        commit_import(
            session,
            parse_export(
                b"03/11/2026, 10:00 - Alice: Eu moro em Recife.\n03/11/2026, 10:01 - Bob: Eu moro em Natal.",
                "DMY",
                "UTC",
            ),
            "indexer-fixture.txt",
            "Synthetic indexing",
            None,
        )


def test_concurrent_indexing_is_unique_and_independent_from_memory_jobs(
    db: sessionmaker[Session],
) -> None:
    ingest(db)
    ready = Barrier(2)

    class ConcurrentEmbedding(MockEmbeddingProvider):
        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            ready.wait(timeout=10)
            return await super().embed_texts(texts)

    def run() -> int:
        return asyncio.run(index_once(db, get_settings(), ConcurrentEmbedding(256)))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(run), executor.submit(run)
        assert first.result(timeout=15) + second.result(timeout=15) == 2
    with db() as session:
        assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 2
        assert session.scalar(select(func.count()).select_from(Memory)) == 0
        assert all(job.processed_messages == 0 for job in session.scalars(select(ImportJob)))
    assert asyncio.run(index_once(db, get_settings(), MockEmbeddingProvider(256))) == 0


def test_edit_during_embedding_never_persists_the_previous_revision(
    db: sessionmaker[Session],
) -> None:
    ingest(db)

    class EditingEmbedding(MockEmbeddingProvider):
        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            with db.begin() as session:
                messages = list(session.scalars(select(Message)))
                for message in messages:
                    invalidate_sources(session, message)
                    message.content = "Mensagem sintética editada."
                    message.revision += 1
            return await super().embed_texts(texts)

    assert asyncio.run(index_once(db, get_settings(), EditingEmbedding(256))) == 0
    with db() as session:
        assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 0
    assert asyncio.run(index_once(db, get_settings(), MockEmbeddingProvider(256))) == 2


@pytest.mark.parametrize("long_body", [False, True])
def test_local_index_batches_are_bounded_without_losing_originals(
    db: sessionmaker[Session], long_body: bool
) -> None:
    from app.config import Settings

    export = "\n".join(
        f"05/11/2026, 10:0{index} - Alice: Aurora {index} "
        + ("dado " * 600 if long_body else "mensagem curta.")
        for index in range(5)
    )
    with db.begin() as session:
        commit_import(
            session,
            parse_export(export.encode(), "DMY", "UTC"),
            "local-batches.txt",
            "Synthetic local indexing",
            None,
        )
    settings = Settings(
        _env_file=None,
        embedding_provider="ollama",
        local_ai_enabled=True,
        embedding_model="synthetic:encoder",
        ollama_embedding_digest="sha256:" + "a" * 64,
        embedding_dimensions=256,
    )

    class RecordingEmbedding(MockEmbeddingProvider):
        def __init__(self) -> None:
            super().__init__(256)
            self.batches: list[list[str]] = []

        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            self.batches.append(texts.copy())
            return await super().embed_texts(texts)

    encoder = RecordingEmbedding()
    total = 0
    for _ in range(6):
        count = asyncio.run(index_once(db, settings, encoder))
        total += count
        if not count:
            break
    else:
        pytest.fail("Local indexing did not finish the finite fixture")
    assert total == 5
    assert all(len(batch) <= 4 and sum(map(len, batch)) <= 8000 for batch in encoder.batches)
    if long_body:
        assert len(encoder.batches[0]) == 2
    with db() as session:
        originals = set(session.scalars(select(Message.content)))
        seen = [text for batch in encoder.batches for text in batch]
        assert len(seen) == 5 and set(seen) == originals
        assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 5
        assert all(job.processed_messages == 0 for job in session.scalars(select(ImportJob)))
