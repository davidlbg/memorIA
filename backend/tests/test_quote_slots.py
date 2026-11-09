import json

import pytest

from app.config import Settings
from app.integrations.local import GroundedProvider
from app.integrations.providers import ProviderError
from app.schemas.domain import GeneratedAnswer

EVIDENCE = [
    {'id': '11111111-1111-4111-8111-111111111111', 'content': 'Quero continuar. Apenas uma vez por mês.', 'sent_at': '2026-10-16T14:00:00Z'},
    {'id': '22222222-2222-4222-8222-222222222222', 'content': 'Fechado.', 'sent_at': '2026-10-16T14:01:00Z'},
]


async def test_quote_slots_preserve_selected_literal_and_never_add_null_sources() -> None:
    async def complete(messages: list[dict[str, str]], decoder: dict[str, object]) -> str:
        slots = decoder['$defs']['LocalClaim']['properties']['supporting_quotes']
        assert slots['type'] == 'object' and slots['additionalProperties'] is False
        assert slots['required'] == ['m1', 'm2']
        assert slots['properties']['m1']['anyOf'][0]['enum'] == ['q1']
        return json.dumps({'claims': [{'supporting_quotes': {'m1': 'q1', 'm2': None}, 'kind': 'interpretation', 'text': 'O participante propõe continuar com frequência mensal.'}]})
    provider = GroundedProvider(Settings(_env_file=None, llm_model='gpt-5.4-mini'), complete)
    answer = await provider.structured('Answer', {'evidence': EVIDENCE}, GeneratedAnswer)
    assert isinstance(answer, GeneratedAnswer)
    assert [str(item) for item in answer.claims[0].source_message_ids] == [EVIDENCE[0]['id']]
    assert answer.claims[0].quote == EVIDENCE[0]['content']


async def test_long_original_is_covered_and_selected_span_remains_literal() -> None:
    original = ' '.join(f'MARCADOR_{index:04d}' for index in range(400))
    evidence = [{**EVIDENCE[0], 'content': original}]
    selected = ''

    async def complete(messages: list[dict[str, str]], decoder: dict[str, object]) -> str:
        nonlocal selected
        options = json.loads(messages[-1]['content'])['evidence'][0]['quote_options']
        assert all(option['text'] in original and len(option['text']) <= 2000 for option in options)
        combined = ' '.join(option['text'] for option in options)
        assert all(f'MARCADOR_{index:04d}' in combined for index in range(400))
        assert 'MARCADOR_' not in json.dumps(decoder)
        selected = next(option['text'] for option in options if option['quote_ref'] == 'q2')
        return json.dumps({'claims': [{'supporting_quotes': {'m1': 'q2'}, 'kind': 'interpretation', 'text': 'O original contém marcadores sintéticos.'}]})
    provider = GroundedProvider(Settings(_env_file=None, llm_model='gpt-5.4-mini'), complete)
    answer = await provider.structured('Answer', {'evidence': evidence}, GeneratedAnswer)
    assert isinstance(answer, GeneratedAnswer) and answer.claims[0].quote == selected
    assert selected in original


@pytest.mark.parametrize('slots', [
    '{"m1":"Frase que não existe.","m2":null}',
    '{"m1":"q999","m2":null}',
    '{"m1":null,"m2":null}',
    '{"m1":"Quero continuar.","m2":null,"m9":null}',
    '{"m1":"Quero continuar."}',
    '{"m1":"Quero continuar.","m1":"Apenas uma vez por mês.","m2":null}',
])
async def test_invalid_quote_slots_are_rejected_without_repair(slots: str) -> None:
    async def complete(messages: list[dict[str, str]], decoder: dict[str, object]) -> str:
        return '{"claims":[{"supporting_quotes":' + slots + ',"kind":"interpretation","text":"Síntese."}]}'
    provider = GroundedProvider(Settings(_env_file=None, llm_model='gpt-5.4-mini'), complete)
    with pytest.raises(ProviderError):
        await provider.structured('Answer', {'evidence': EVIDENCE}, GeneratedAnswer)
