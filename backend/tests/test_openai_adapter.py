import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.integrations.providers import OpenAIEmbeddingProvider, OpenAIProvider, ProviderError
from app.schemas.domain import SourceMessage


def configured() -> Settings:
    return Settings(
        _env_file=None,
        external_ai_enabled=True,
        openai_api_key=SecretStr("synthetic-not-a-real-key"),
    )


async def test_transport_blocks_external_calls_without_explicit_consent() -> None:
    with pytest.raises(ProviderError, match="explicitly disabled"):
        await OpenAIEmbeddingProvider(
            Settings(_env_file=None, external_ai_enabled=False)
        ).embed_texts(["synthetic"])


@pytest.mark.parametrize("model", ["gpt-5.4-mini-2026-03-17", "gpt-6.1-sol"])
def test_query_model_override_never_changes_worker_or_embedding_models(model: str) -> None:
    from app.integrations.providers import providers
    settings = configured().model_copy(update={'llm_provider': 'openai', 'embedding_provider': 'openai', 'openai_query_model': model})
    worker, background_embeddings = providers(settings)
    query, query_embeddings = providers(settings, for_query=True)
    assert isinstance(worker, OpenAIProvider) and isinstance(query, OpenAIProvider)
    assert worker.settings.llm_model == settings.llm_model == 'gpt-4.1-mini'
    assert query.settings.llm_model == model
    assert isinstance(background_embeddings, OpenAIEmbeddingProvider) and isinstance(query_embeddings, OpenAIEmbeddingProvider)
    assert background_embeddings.settings.embedding_identity == query_embeddings.settings.embedding_identity == settings.embedding_identity


async def test_openai_structured_json_and_embedding_wire_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = SourceMessage(
        id=uuid4(),
        conversation_id=uuid4(),
        sender_name_snapshot="Alice",
        content="I work as an engineer.",
        sender_contact_id=uuid4(),
        from_me=True,
        connector_metadata={"quoted_message_id": "missing-parent", "remote_jid": "opaque-not-for-the-model"},
        message_type="text",
        sent_at=datetime(2026, 10, 31, tzinfo=UTC),
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        payload = json.loads(request.content)
        if request.url.path.endswith("embeddings"):
            assert payload["dimensions"] == 256
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 256}]})
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["store"] is False
        assert payload["temperature"] == 0
        wire_input = json.loads(payload["messages"][-1]["content"])[0]
        assert wire_input["id"] == "m1" and wire_input["sender_ref"] == "p1"
        assert wire_input["from_me"] is True and wire_input["quoted_message_unresolved"] is True
        assert "opaque-not-for-the-model" not in json.dumps(payload)
        assert str(message.id) not in json.dumps(payload)
        memory = {
            "memory_type": "fact", "subject": "Alice", "content": message.content,
            "confidence": 0.9, "evidence_basis": "interpretation",
            "current_quote": {"message_id": "m1", "quote": message.content},
            "supporting_quotes": [],
        }
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"memories": [memory]})}}]}
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "app.integrations.providers.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )
    extracted = await OpenAIProvider(configured()).extract_memories([message])
    assert extracted.memories[0].source_message_ids == [message.id]
    vector = await OpenAIEmbeddingProvider(configured()).embed_texts([message.content])
    assert len(vector) == 1 and len(vector[0]) == 256
    assert calls == ["/v1/chat/completions", "/v1/embeddings"]


async def test_openai_transient_failure_retries_and_invalid_vectors_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"error": "synthetic rate limit"})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 2]}]})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "app.integrations.providers.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(ProviderError, match="dimensions"):
        await OpenAIEmbeddingProvider(configured()).embed_texts(["synthetic"])
    assert attempts == 2


@pytest.mark.parametrize("model", ["gpt-5.4-mini", "gpt-5.4-2026-03-05", "gpt-6.1-sol"])
async def test_responses_replays_opaque_reasoning_and_resets_between_queries(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/responses'
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload['store'] is False and payload['reasoning'] == {'effort': 'low'}
        assert 'temperature' not in payload and 'previous_response_id' not in payload
        if len(requests) == 2:
            assert payload['input'][1]['encrypted_content'] == 'synthetic-opaque-state'
            assert payload['input'][2]['call_id'] == 'call_1'
            assert payload['input'][3] == {'type': 'function_call_output', 'call_id': 'call_1', 'output': '{"contacts":[]}'}
            assert payload['instructions'] == 'Updated remaining budget'
        if len(requests) == 3:
            assert payload['input'] == [{'role': 'user', 'content': 'Independent new query'}]
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'reasoning', 'id': 'rs_1', 'summary': [], 'encrypted_content': 'synthetic-opaque-state'},
            {'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_1', 'name': 'find_contacts', 'arguments': '{"name":"fixture"}', 'status': 'completed'},
        ]})

    client_type = httpx.AsyncClient
    monkeypatch.setattr('app.integrations.providers.httpx.AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    provider = OpenAIProvider(configured().model_copy(update={'llm_model': model}))
    tools = [{'type': 'function', 'function': {'name': 'find_contacts', 'description': 'fixture', 'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}, 'strict': True}}]
    messages = [{'role': 'system', 'content': 'Initial instructions'}, {'role': 'user', 'content': 'First query'}]
    turn = await provider.research_step(messages, tools)
    messages[0]['content'] = 'Updated remaining budget'
    messages += [turn.model_dump(exclude_none=True), {'role': 'tool', 'tool_call_id': 'call_1', 'content': '{"contacts":[]}'}]
    await provider.research_step(messages, tools)
    await provider.research_step([{'role': 'system', 'content': 'New instructions'}, {'role': 'user', 'content': 'Independent new query'}], tools)
    with pytest.raises(ProviderError, match='invalid or incomplete'):
        await provider.research_step([*messages[:-1], {'role': 'tool', 'tool_call_id': 'wrong-call', 'content': '{}'}], tools)
    assert len(requests) == 3


@pytest.mark.parametrize('refused', [False, True])
@pytest.mark.parametrize('model', ['gpt-5.4-mini', 'gpt-5.4-2026-03-05', 'gpt-6.1-sol'])
async def test_responses_structured_answer_validates_completion_and_refusal(monkeypatch: pytest.MonkeyPatch, refused: bool, model: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload['text']['format']['type'] == 'json_schema'
        assert payload['text']['format']['strict'] is True and payload['store'] is False
        assert 'temperature' not in payload
        return httpx.Response(200, json={'status': 'completed' if refused else 'incomplete', 'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'refusal', 'refusal': 'Synthetic refusal'}]}]})
    client_type = httpx.AsyncClient
    monkeypatch.setattr('app.integrations.providers.httpx.AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    provider = OpenAIProvider(configured().model_copy(update={'llm_model': model}))
    with pytest.raises(ProviderError, match='incomplete, refused or invalid'):
        await provider._complete([{'role': 'user', 'content': 'Synthetic'}], {'type': 'object', 'properties': {}})
