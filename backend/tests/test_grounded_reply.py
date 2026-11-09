import json
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core.evidence import FocusedReply, QuoteRegistry
from app.core.ingestion.parser import parse_export
from app.core.retrieval.research import ResearchGateway, ToolRequestError
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider
from app.integrations.research import ResearchCall, ResearchFunction, ResearchTurn
from app.models.entities import Conversation, Message
from app.schemas.domain import Query, SourceMessage
from app.services.imports import commit_import
from app.services.knowledge import answer_query

pytestmark = pytest.mark.database





def seed(db: sessionmaker[Session], count: int = 520) -> UUID:

    lines = [f"01/09/2026, {i // 60:02}:{i % 60:02} - Ana: Fato sintético {i}." for i in range(count)]

    with db.begin() as session:

        return commit_import(session, parse_export("\n".join(lines).encode(), "DMY", "UTC"), "fixture.txt", "SINTÉTICO", None).conversation_id





async def test_transcript_pages_are_complete_ordered_and_cursor_scoped(db: sessionmaker[Session]) -> None:

    cid = seed(db)

    with db() as session:

        gateway = ResearchGateway(session, get_settings(), Query(question="contexto", conversation_id=cid), MockEmbeddingProvider(256))

        conv = session.get(Conversation, cid)

        assert conv

        ref = gateway.conversation_ref(conv)

        cursor = None

        rows = []

        while True:

            result = await gateway.execute("read_conversation", json.dumps({"conversation_ref": ref, "after_cursor": cursor}))

            assert result["available_messages"] == 520

            rows.extend(result["messages"])

            cursor = result["next_cursor"]

            if not cursor:

                break

        assert len(rows) == len(gateway.messages) == 520

        assert len({row[0] for row in rows}) == 520

        assert [row[6][0]["text"] for row in rows] == [f"Fato sintético {i}." for i in range(520)]

        with pytest.raises(ToolRequestError):

            await gateway.execute("read_conversation", json.dumps({"conversation_ref": ref, "after_cursor": "page1", "date_from": "2026-09-01T01:00:00Z"}))

        with pytest.raises(ToolRequestError):

            await gateway.execute("read_conversation", json.dumps({"conversation_ref": "v999"}))





class DirectReply(MockLLMProvider):

    supports_grounded_answers = True



    def __init__(self) -> None:

        self.turn = 0



    async def generate(self, *args: object, **kwargs: object) -> object:

        raise AssertionError("A directly grounded reply must not trigger another synthesis call")



    async def research_step(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ResearchTurn:

        self.turn += 1

        if self.turn == 1:

            name, args = "list_conversations", {"name": None, "offset": 0}

        elif self.turn == 2:

            result = json.loads(str(messages[-1]["content"]))

            name, args = "read_conversation", {"conversation_ref": result["conversations"][0]["conversation_ref"]}

        else:

            result = json.loads(str(messages[-1]["content"]))

            name, args = "answer_question", {"response_mode": "overview", "claims": [{"section": "Contexto", "text": "Fato sintético 0.", "quote_refs": [result["messages"][0][6][0]["quote_ref"]]}]}

        return ResearchTurn(role="assistant", tool_calls=[ResearchCall(id=f"call-{self.turn}", type="function", function=ResearchFunction(name=name, arguments=json.dumps(args)))])





async def test_direct_answer_preserves_hidden_revision_checks_without_second_generation(db: sessionmaker[Session]) -> None:

    cid = seed(db, 3)

    with db() as session:

        answer = await answer_query(session, get_settings(), DirectReply(), MockEmbeddingProvider(256), Query(question="visão geral", conversation_id=cid, compact_context=True))

        assert not answer.insufficient_evidence

        assert len(answer.sources) == 1 and not answer.context_sources

        assert len(answer.context_revisions) == 2

        assert answer.coverage[0].complete

        assert answer.response_mode == "overview"

        assert answer.claims[0].supporting_quotes[0].quote == answer.sources[0].content

        assert [step.tool for step in answer.research_steps] == ["list_conversations", "read_conversation", "answer_question"]





async def test_overview_reports_partial_coverage(db: sessionmaker[Session]) -> None:

    cid = seed(db, 520)

    with db() as session:

        answer = await answer_query(session, get_settings().model_copy(update={"research_max_steps":3}), DirectReply(), MockEmbeddingProvider(256), Query(question="visão geral", conversation_id=cid, compact_context=True))

        assert answer.research_limited and not answer.coverage[0].complete

        assert answer.coverage[0].inspected_messages == 281

        assert answer.coverage[0].available_messages == 520





def test_quote_registry_preserves_multiple_spans_and_rejects_unknowns(db: sessionmaker[Session]) -> None:

    cid = seed(db, 1)

    with db() as session:

        message = session.scalar(select(Message).where(Message.conversation_id == cid))

        assert message

        source = SourceMessage.model_validate(message).model_copy(update={"content": "A" * 2100 + "último detalhe"})

        registry = QuoteRegistry()

        options = registry.register("m1", source)

        reply = FocusedReply.model_validate({"response_mode": "focused", "temporal_scope": "not_time_sensitive", "claims": [{"text": "Detalhe", "quote_refs": [option["quote_ref"] for option in options]}]})

        answer, sources = registry.answer(reply)

        assert len(sources) == 1 and answer.claims[0].source_message_ids == [source.id]

        assert len(answer.claims[0].supporting_quotes) == len(options) > 1

        assert options[-1]["text"].endswith("último detalhe")

        unknown = reply.model_copy(update={"claims": [reply.claims[0].model_copy(update={"quote_refs": ["m2:q1"]})]})

        with pytest.raises(ValueError):

            registry.answer(unknown)

        with pytest.raises(ValidationError):

            FocusedReply.model_validate({"response_mode": "focused", "temporal_scope": "not_time_sensitive", "claims": [{"section": "Biografia", "text": "Detalhe", "quote_refs": ["m1:q1"]}]})



async def test_transcript_budget_stops_with_visible_remaining_history(db: sessionmaker[Session]) -> None:
    cid = seed(db, 1100)
    with db() as session:
        gateway = ResearchGateway(session, get_settings(), Query(question="contexto", conversation_id=cid), MockEmbeddingProvider(256))
        conv = session.get(Conversation, cid)
        assert conv
        ref = gateway.conversation_ref(conv)
        cursor = None
        for _ in range(4):
            result = await gateway.execute("read_conversation", json.dumps({"conversation_ref": ref, "after_cursor": cursor}))
            cursor = result["next_cursor"]
        assert len(gateway.messages) == 1024
        assert result["has_more"] and result["budget_exhausted"] and cursor is None
        assert result["remaining_message_budget"] == 0
        assert any(source.content == "Fato sintético 1099." for source in gateway.messages.values())


async def test_direct_answer_revalidates_even_uncited_inspected_messages(db: sessionmaker[Session]) -> None:
    from app.integrations.providers import ProviderError
    cid = seed(db, 3)
    class EditedReply(DirectReply):
        async def research_step(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ResearchTurn:
            turn = await super().research_step(messages, tools)
            if self.turn == 3:
                with db.begin() as writer:
                    row = writer.scalar(select(Message).where(Message.conversation_id == cid).order_by(Message.sequence.desc()).limit(1))
                    assert row
                    row.content = "Edited synthetic uncited message."
                    row.revision += 1
            return turn
    with db() as session, pytest.raises(ProviderError, match="Evidence changed"):
        await answer_query(session, get_settings(), EditedReply(), MockEmbeddingProvider(256), Query(question="contexto", conversation_id=cid, compact_context=True))


async def test_portuguese_search_handles_inflected_words_without_history_embeddings(db: sessionmaker[Session]) -> None:
    from app.core.retrieval.search import search
    with db.begin() as s:
        inside = commit_import(s, parse_export(b"01/09/2026, 10:00 - Ana: Moro em Tiradentes.", "DMY", "UTC"), "morphology.txt", "SINTETICO morphology", None).conversation_id
        outside = commit_import(s, parse_export(b"01/09/2026, 10:00 - Bia: Moro em Recife.", "DMY", "UTC"), "other.txt", "SINTETICO outside", None).conversation_id
    with db() as s:
        ids = await search(s, get_settings(), MockEmbeddingProvider(256), kind="messages", query="morar", conversation_id=inside)
        assert len(ids) == 1
        row = s.get(Message, ids[0])
        assert row and row.conversation_id == inside and row.conversation_id != outside


async def test_current_state_cannot_finish_before_later_originals_are_read(db: sessionmaker[Session]) -> None:
    from app.schemas.domain import ResearchStep
    cid = seed(db, 520)
    with db() as s:
        gateway = ResearchGateway(s, get_settings(), Query(question="estado atual", conversation_id=cid), MockEmbeddingProvider(256), direct_answers=True)
        conv = s.get(Conversation, cid)
        assert conv
        ref = gateway.conversation_ref(conv)
        await gateway.execute("read_conversation", json.dumps({"conversation_ref": ref}))
        gateway.result.steps.append(ResearchStep(tool="read_conversation", results=256))
        old_ref = next(ref for ref, source in gateway.messages.items() if source.content == "Fato sintético 0.")
        args = {"response_mode":"focused", "temporal_scope":"current", "claims":[{"text":"Estado antigo.", "quote_refs":[old_ref+":q1"]}]}
        blocked = await gateway.execute("answer_specific", json.dumps(args))
        assert blocked["needs_review"] and blocked["reason"] == "later_messages_not_reviewed" and not gateway.done
        review = blocked["required_reviews"][0]
        assert review["unread_messages"] == 239
        assert len(gateway.messages) == 520
        latest = next(ref for ref, source in gateway.messages.items() if source.content == "Fato sintético 519.")
        args["claims"] = [{"text":"Estado atualizado.","quote_refs":[latest+":q1"]}]
        accepted = await gateway.execute("answer_specific", json.dumps(args))
        assert accepted["finished"] and gateway.done


async def test_overview_cannot_stop_early_while_unread_pages_fit(db: sessionmaker[Session]) -> None:
    from app.schemas.domain import ResearchStep
    cid = seed(db, 520)
    with db() as s:
        gateway = ResearchGateway(s, get_settings(), Query(question="visão geral", conversation_id=cid), MockEmbeddingProvider(256), direct_answers=True)
        conv = s.get(Conversation, cid)
        assert conv
        ref = gateway.conversation_ref(conv)
        page = await gateway.execute("read_conversation", json.dumps({"conversation_ref":ref}))
        gateway.result.steps.append(ResearchStep(tool="read_conversation", results=256))
        args = {"response_mode":"overview", "claims":[{"section":"Contexto","text":"Fato sintético 0.", "quote_refs":[page["messages"][0][6][0]["quote_ref"]]}]}
        blocked = await gateway.execute("answer_question", json.dumps(args))
        assert blocked["needs_review"] and blocked["reason"] == "overview_pages_not_finished" and not gateway.done
        review = blocked["required_reviews"][0]
        assert review["unread_messages"] == 239
        assert len(gateway.messages) == 520
        assert (await gateway.execute("answer_question",json.dumps(args)))["finished"]
