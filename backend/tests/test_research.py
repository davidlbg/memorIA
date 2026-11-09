import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core.ingestion.parser import parse_export
from app.core.retrieval.research import ResearchGateway, ToolRequestError
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider, ProviderError
from app.integrations.research import ResearchCall, ResearchFunction, ResearchTurn
from app.models.entities import Contact, Conversation, Message
from app.schemas.domain import Query
from app.services.imports import commit_import
from app.services.knowledge import answer_query

pytestmark = pytest.mark.database


def fixture(db: sessionmaker[Session]) -> tuple[str, str]:
    with db.begin() as session:
        real = commit_import(session, parse_export("05/11/2026, 14:00 - Trovão: Deixei o pacote na portaria.\n05/11/2026, 14:01 - Ana: Vou buscar depois do almoço.".encode(), "DMY", "UTC"), "real-fixture.txt", "SINTETICO contato", None)
        decoy = commit_import(session, parse_export("01/01/2025, 10:00 - Outra: Jorge Trovão é um personagem e gosta de tudo que tem trovão.".encode(), "DMY", "UTC"), "decoy.txt", "SINTETICO menção", None)
        contact = session.scalar(select(Contact).where(Contact.conversation_id == real.conversation_id, Contact.display_name == "Trovão"))
        assert contact
        # Reproduce the real bug: registry started as an opaque native ID, but
        # original authorship has the later observed nickname.
        contact.display_name = "fixture-contact@lid"
        contact.external_identifier = "fixture-contact@lid"
        conv = session.get(Conversation, real.conversation_id)
        assert conv
        conv.conversation_type = "direct"
        conv.title = "fixture-chat@lid"
        return str(real.conversation_id), str(decoy.conversation_id)


def call(name: str, arguments: dict[str, object]) -> ResearchTurn:
    return ResearchTurn(role="assistant", tool_calls=[ResearchCall(id="fixture-call", type="function", function=ResearchFunction(name=name, arguments=json.dumps(arguments)))])


class ContactResearch(MockLLMProvider):
    def __init__(self) -> None:
        self.turn = 0

    async def research_step(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ResearchTurn:
        self.turn += 1
        if self.turn == 1:
            return call("find_contacts", {"name": "Trovão", "offset": 0})
        result = json.loads(str(messages[-1]["content"]))
        if self.turn == 2:
            assert result["total_candidates"] == 1
            contact = result["contacts"][0]
            assert contact["observed_author_names"] == ["Trovão"]
            assert contact["display_name"] is None
            assert "fixture-contact@lid" not in str(result)
            return call("read_messages", {"conversation_ref": contact["conversation_ref"], "before_ref": None, "date_from": None, "date_to": None, "limit": 15})
        return call("finish_research", {"message_refs": [row["message_ref"] for row in result["messages"]], "ambiguous_contact_refs": []})


async def test_identity_research_uses_authorship_not_a_mention_in_another_chat(db: sessionmaker[Session]) -> None:
    real, decoy = fixture(db)
    with db() as session:
        answer = await answer_query(session, get_settings(), ContactResearch(), MockEmbeddingProvider(256), Query(question="Chat, quem é Trovão?", previous_question="Quem é Jorge?"))
        assert not answer.insufficient_evidence
        assert answer.sources and all(str(row.conversation_id) == real for row in answer.sources)
        assert all(str(row.conversation_id) != decoy for row in answer.context_sources)
        assert [step.tool for step in answer.research_steps] == ["find_contacts", "read_messages", "finish_research"]


async def test_gateway_enforces_scope_dates_and_registered_references(db: sessionmaker[Session]) -> None:
    real, decoy = fixture(db)
    with db() as session:
        from uuid import UUID
        gateway = ResearchGateway(session, get_settings(), Query(question="hoje", conversation_id=UUID(real), date_from=datetime(2026, 11, 5, 14, 1, tzinfo=UTC)), MockEmbeddingProvider(256))
        outside = session.get(Conversation, UUID(decoy))
        inside = session.get(Conversation, UUID(real))
        assert inside and outside
        outside_ref, inside_ref = gateway.conversation_ref(outside), gateway.conversation_ref(inside)
        with pytest.raises(ToolRequestError):
            await gateway.execute("read_messages", json.dumps({"conversation_ref": outside_ref, "limit": 15}))
        with pytest.raises(ToolRequestError):
            await gateway.execute("execute_sql", '{"query":"delete from messages"}')
        response = await gateway.execute("read_messages", json.dumps({"conversation_ref": inside_ref, "date_from": "2020-01-01T00:00:00Z", "limit": 15}))
        assert len(gateway.messages) == 1
        assert response["omitted"] == 0
        with pytest.raises(ToolRequestError):
            await gateway.execute("finish_research", '{"message_refs":["m999"],"ambiguous_contact_refs":[]}')


async def test_research_limit_is_visible_and_never_fabricates_an_answer(db: sessionmaker[Session]) -> None:
    fixture(db)
    class NeverFinish(MockLLMProvider):
        async def research_step(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ResearchTurn:
            return call("find_contacts", {"name": "não-existe", "offset": 0})
    with db() as session:
        answer = await answer_query(session, get_settings().model_copy(update={"research_max_steps": 2}), NeverFinish(), MockEmbeddingProvider(256), Query(question="quem?"))
        assert answer.insufficient_evidence and answer.research_limited and answer.clarification
        assert len(answer.research_steps) == 2 and not answer.claims


async def test_changed_uncited_research_input_invalidates_the_response(db: sessionmaker[Session]) -> None:
    fixture(db)
    class EditingResearch(ContactResearch):
        async def research_step(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ResearchTurn:
            result = await super().research_step(messages, tools)
            if self.turn == 3:
                returned = json.loads(str(messages[-1]["content"]))["messages"]
                with db.begin() as update_session:
                    other = update_session.scalar(select(Message).where(Message.content == "Vou buscar depois do almoço."))
                    assert other
                    other.content = "Texto sintético alterado."
                    other.revision += 1
                return call("finish_research", {"message_refs": [returned[0]["message_ref"]], "ambiguous_contact_refs": []})
            return result
    with db() as session, pytest.raises(ProviderError, match="Evidence changed"):
        await answer_query(session, get_settings(), EditingResearch(), MockEmbeddingProvider(256), Query(question="Quem é Trovão?"))


async def test_last_call_selects_collected_evidence_instead_of_discarding_it(db: sessionmaker[Session]) -> None:
    real, _ = fixture(db)

    class BudgetAwareResearch(ContactResearch):
        async def research_step(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ResearchTurn:
            if len(tools) == 1:
                assert tools[0]['function']['name'] == 'finish_research'
                returned = json.loads(str(messages[-1]['content']))['messages']
                return call('finish_research', {'message_refs': [row['message_ref'] for row in returned], 'ambiguous_contact_refs': []})
            return await super().research_step(messages, tools)

    with db() as session:
        answer = await answer_query(session, get_settings().model_copy(update={'research_max_steps': 3}), BudgetAwareResearch(), MockEmbeddingProvider(256), Query(question='Quem é Trovão?'))
        assert answer.claims and not answer.insufficient_evidence
        assert all(str(row.conversation_id) == real for row in answer.sources)
        assert [step.tool for step in answer.research_steps] == ['find_contacts', 'read_messages', 'finish_research']


async def test_search_pagination_reaches_older_originals_without_indexing(db: sessionmaker[Session]) -> None:
    from datetime import timedelta
    from uuid import UUID

    start = datetime(2026, 8, 1, 10, tzinfo=UTC)
    text = '\n'.join(f'{start + timedelta(days=index):%d/%m/%Y, %H:%M} - Lia: CronologiaZephyr decisão etapa {index}.' for index in range(36))
    with db.begin() as session:
        job = commit_import(session, parse_export(text.encode(), 'DMY', 'UTC'), 'pagination.txt', 'SINTETICO paginação', None)
        conversation = str(job.conversation_id)
    with db() as session:
        gateway = ResearchGateway(session, get_settings(), Query(question='CronologiaZephyr', conversation_id=UUID(conversation)), MockEmbeddingProvider(256))
        references = []
        for offset, expected_count, has_more in [(0, 15, True), (15, 15, True), (30, 6, False)]:
            result = await gateway.execute('search_messages', json.dumps({'query': 'CronologiaZephyr', 'offset': offset}))
            assert len(result['messages']) == expected_count
            assert result['has_more'] is has_more
            references.extend(row['message_ref'] for row in result['messages'])
            assert result['coverage'] == 'ranked_candidates_not_exhaustive'
        assert len(references) == len(set(references)) == 36
        assert len(gateway.messages) == 36
        assert any('etapa 0.' in row.content for row in gateway.messages.values())


async def test_timeline_preserves_intermediate_month_and_hard_date_scope(db: sessionmaker[Session]) -> None:
    from datetime import timedelta

    start = datetime(2026, 8, 1, 10, tzinfo=UTC)
    lines = [f'{start + timedelta(days=index):%d/%m/%Y, %H:%M} - Lia: Atlas Atlas Atlas Atlas Atlas Atlas Atlas Atlas rotina sem decisão.' for index in range(92)]
    lines += ['03/08/2026, 14:00 - Lia: Atlas lançamento inicial em agosto.', '14/09/2026, 14:00 - Lia: Atlas lançamento cancelado pela auditoria.', '19/10/2026, 14:00 - Lia: Atlas lançamento confirmado para novembro.']
    with db.begin() as session:
        job = commit_import(session, parse_export('\n'.join(lines).encode(), 'DMY', 'UTC'), 'timeline.txt', 'SINTETICO linha temporal', None)
        conversation_id = job.conversation_id
    with db() as session:
        gateway = ResearchGateway(session, get_settings(), Query(question='Como mudou?', conversation_id=conversation_id), MockEmbeddingProvider(256))
        result = await gateway.execute('search_timeline', json.dumps({'query': 'Atlas lançamento', 'per_month': 3}))
        assert [period['month'] for period in result['periods']] == ['2026-08', '2026-09', '2026-10']
        assert [period['matching_messages'] for period in result['periods']] == [32, 31, 32]
        assert len(gateway.messages) > 9
        assert sum(source.retrieval_role == 'match' for source in gateway.messages.values()) == 9
        assert sum('lançamento' in row.content for row in gateway.messages.values()) == 3
        assert any('cancelado pela auditoria' in row.content for row in gateway.messages.values())
        assert not result['older_periods_omitted']
        scoped = ResearchGateway(session, get_settings(), Query(question='Até agosto?', conversation_id=conversation_id, date_to=datetime(2026, 8, 31, 23, tzinfo=UTC)), MockEmbeddingProvider(256))
        historical = await scoped.execute('search_timeline', json.dumps({'query': 'Atlas lançamento', 'date_to': '2027-01-01T00:00:00Z'}))
        assert len(historical['periods']) == 1
        assert all(source.sent_at.month == 8 for source in scoped.messages.values())


async def test_wrong_year_returns_actual_scoped_history_before_embedding(db: sessionmaker[Session]) -> None:
    from uuid import UUID
    real, _ = fixture(db)
    class NoEmbeddingCalls(MockEmbeddingProvider):
        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            raise AssertionError('A disjoint date range must not call the provider')
    with db() as session:
        gateway = ResearchGateway(session, get_settings(), Query(question='Como mudou?', conversation_id=UUID(real)), NoEmbeddingCalls(256))
        result = await gateway.execute('search_messages', json.dumps({'query': 'pacote', 'date_from': '2023-08-01T00:00:00Z', 'date_to': '2023-11-01T00:00:00Z'}))
        assert result['error'] == 'requested_dates_outside_available_history'
        assert result['available_history']['first_message_at'].startswith('2026-11-05')
        assert not gateway.messages


async def test_timeline_includes_short_acceptance_as_context_without_keyword_match(db: sessionmaker[Session]) -> None:
    text = '16/10/2026, 14:00 - Bia: Podemos retomar encontros mensais em novembro.\n16/10/2026, 14:01 - Nilo: Fechado, com aviso antecipado.'
    with db.begin() as session:
        job = commit_import(session, parse_export(text.encode(), 'DMY', 'UTC'), 'short-acceptance.txt', 'SINTETICO aceitação', None)
        conversation_id = job.conversation_id
    with db() as session:
        gateway = ResearchGateway(session, get_settings(), Query(question='Qual acordo?', conversation_id=conversation_id), MockEmbeddingProvider(256))
        result = await gateway.execute('search_timeline', json.dumps({'query': 'encontros mensais'}))
        assert len(result['messages']) == 2
        accepted = next(source for source in gateway.messages.values() if source.sender_name_snapshot == 'Nilo')
        assert accepted.content == 'Fechado, com aviso antecipado.' and accepted.retrieval_role == 'context'
        assert all(source.conversation_id == conversation_id for source in gateway.messages.values())


async def test_search_next_offset_remains_valid_through_last_candidate(db: sessionmaker[Session]) -> None:
    from datetime import timedelta
    start = datetime(2026, 7, 1, 10, tzinfo=UTC)
    text = '\n'.join(f'{start + timedelta(days=index):%d/%m/%Y, %H:%M} - Lia: PaginaAtlas etapa {index}.' for index in range(100))
    with db.begin() as session:
        job = commit_import(session, parse_export(text.encode(), 'DMY', 'UTC'), 'last-page.txt', 'SINTETICO última página', None)
        conversation_id = job.conversation_id
    with db() as session:
        gateway = ResearchGateway(session, get_settings(), Query(question='PaginaAtlas', conversation_id=conversation_id), MockEmbeddingProvider(256))
        offset = 80
        seen = []
        while offset is not None:
            result = await gateway.execute('search_messages', json.dumps({'query': 'PaginaAtlas', 'offset': offset, 'limit': 1}))
            assert len(result['messages']) == 1
            seen.append(result['messages'][0]['message_ref'])
            offset = result['next_offset']
        assert len(seen) == len(set(seen)) == 20
