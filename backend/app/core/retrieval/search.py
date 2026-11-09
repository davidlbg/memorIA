import re
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.providers import EmbeddingProvider, validate_vectors
from app.models.entities import Memory, MemoryEmbedding, Message, MessageEmbedding
from app.schemas.domain import MemoryType

STOPWORDS = frozenset(
    "o a os as de do da dos das e um uma que qual quais quem como quando onde em no na nos nas com para por eu meu minha se foi é era ser sobre the an of and or to in on is are was were what when where how who about my i".split()
)


def rank_fusion(lists: list[list[UUID]], limit: int) -> list[UUID]:
    scores: dict[UUID, float] = {}
    for ranking in lists:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0) + 1 / (60 + rank)
    return sorted(scores, key=lambda item: (-scores[item], str(item)))[:limit]


async def search(
    session: Session,
    settings: Settings,
    embeddings: EmbeddingProvider,
    *,
    kind: str,
    query: str,
    contact_id: UUID | None = None,
    conversation_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    memory_type: MemoryType | None = None,
    limit: int = 20,
) -> list[UUID]:
    entity: type[Message] | type[Memory]
    embedding_entity: type[MessageEmbedding] | type[MemoryEmbedding]
    if kind == "messages":
        entity, embedding_entity = Message, MessageEmbedding
        time_column = Message.sent_at
        contact_column = Message.sender_contact_id
        join = MessageEmbedding.message_id == Message.id
    else:
        entity, embedding_entity = Memory, MemoryEmbedding
        time_column = Memory.observed_at
        contact_column = Memory.subject_contact_id
        join = MemoryEmbedding.memory_id == Memory.id
    conditions = []
    if kind == "messages":
        conditions.append(Message.delivery_state == "active")
    if kind != "messages":
        conditions.append(Memory.status != "deleted")
    if contact_id:
        conditions.append(contact_column == contact_id)
    if conversation_id:
        conditions.append(entity.conversation_id == conversation_id)
    if date_from:
        conditions.append(time_column >= date_from)
    if date_to:
        conditions.append(time_column <= date_to)
    if memory_type and kind != "messages":
        conditions.append(Memory.memory_type == memory_type)
    base = select(entity.id).where(*conditions)
    if not query.strip():
        return list(session.scalars(base.order_by(time_column.desc(), entity.id).limit(limit)))
    # OR tokens permit natural-language queries without requiring every stopword.
    tokens = [token for token in re.findall(r"\w+", query.casefold()) if token not in STOPWORDS][
        :40
    ]
    if not tokens:
        return []
    keyword_lists = []
    # Keep exact names/identifiers, plus Portuguese morphology for natural
    # questions (morar/moro, revisar/revisão). This reads existing originals;
    # it neither reprocesses history nor calls an external indexer.
    for configuration in ("simple", "portuguese"):
        tsquery = func.to_tsquery(configuration, " | ".join(tokens))
        document = func.to_tsvector(configuration, entity.content)
        keyword = (
            base.where(document.op("@@")(tsquery))
            .order_by(func.ts_rank_cd(document, tsquery).desc(), time_column.desc(), entity.id)
            .limit(100)
        )
        keyword_lists.append(list(session.scalars(keyword)))
    vectors = [await embeddings.embed_query(query)]
    validate_vectors(vectors, 1, settings.embedding_dimensions)
    distance = embedding_entity.embedding.cosine_distance(vectors[0])
    semantic_ids = list(
        session.scalars(
            base.join(embedding_entity, join)
            .where(
                embedding_entity.model_name == settings.embedding_identity,
                distance < (0.9 if settings.embedding_provider == "mock" else 0.65),
            )
            .order_by(distance, entity.id)
            .limit(100)
        )
    )
    return rank_fusion([*keyword_lists, semantic_ids], limit)
