import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.evidence import original_spans
from app.core.ingestion.parser import parse_export
from app.integrations.errors import BudgetConfigurationError, BudgetExceeded, ProviderError
from app.integrations.providers import MockEmbeddingProvider, MockLLMProvider, OpenAITransport
from app.models.entities import (
    AIBudgetPeriod,
    AIUsage,
    ImportJob,
    Memory,
    Message,
    MessageEmbedding,
)
from app.services import usage
from app.services.imports import commit_import
from app.workers.indexer import index_once
from app.workers.runner import run_once

pytestmark = pytest.mark.database
PAYLOAD = {"model": "gpt-6.1-sol", "max_output_tokens": 6000, "input": "SYNTHETIC ONLY"}


def configured(**changes: object) -> Settings:
    settings = get_settings().model_copy(update={
        "external_ai_enabled": True,
        "openai_api_key": SecretStr("synthetic-budget-key"),
        "ai_monthly_budget_usd": Decimal("50"),
        "ai_budget_prior_spend_usd": Decimal("7.47"),
        "ai_budget_prior_spend_month": usage.clock().strftime("%Y-%m"),
        "openai_query_model": "gpt-6.1-sol",
    })
    return settings.model_copy(update=changes)


def receipt() -> dict[str, object]:
    return {"model": "gpt-6.1-sol", "service_tier": "default", "usage": {
        "input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200,
        "input_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100},
        "output_tokens_details": {"reasoning_tokens": 120},
    }}


def test_reservations_are_atomic_for_two_users_and_release_only_confirmed_cost(db: sessionmaker[Session]) -> None:
    settings = configured(ai_monthly_budget_usd=Decimal("0.01"), ai_budget_prior_spend_usd=Decimal(0))
    gate = Barrier(2)
    def ask() -> object:
        gate.wait(timeout=5)
        try:
            return usage.reserve(settings, "embeddings", {"model": "text-embedding-3-small"}, factory=db)
        except BudgetExceeded:
            return "blocked"
    with ThreadPoolExecutor(max_workers=2) as executor:
        answers = list(executor.map(lambda _: ask(), range(2)))
    assert answers.count("blocked") == 1
    with db() as session:
        records = list(session.scalars(select(AIUsage)))
        assert len(records) == 1 and records[0].status == "reserved"
        identifier = records[0].id
        state = usage.budget_status(session, settings)
        assert state.reserved_usd == "0.006000"
    usage.finish(identifier, {"model": "text-embedding-3-small", "usage": {"prompt_tokens": 50, "total_tokens": 50}}, factory=db)
    assert usage.reserve(settings, "embeddings", {"model": "text-embedding-3-small"}, factory=db)
    with db() as session:
        state = usage.budget_status(session, settings)
        assert state.recorded_usd == "0.000001" and state.reserved_usd == "0.006000"


async def test_metered_transport_accounts_reasoning_once_and_enforces_standard_cache(db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage, "session_factory", lambda: db)
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["service_tier"] == "default"
        assert payload["prompt_cache_options"] == {"mode": "implicit", "ttl": "30m"}
        return httpx.Response(200, json=receipt())
    client = httpx.AsyncClient
    monkeypatch.setattr("app.integrations.providers.httpx.AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    settings = configured()
    with usage.usage_operation() as identifier:
        await OpenAITransport(settings).post("responses", PAYLOAD)
    with db() as session:
        row = session.scalar(select(AIUsage))
        assert row and row.status == "settled"
        # Output already includes the 120 reasoning tokens: no double charge.
        assert row.cost_micro_usd == 2910 and row.reasoning_tokens == 120
        assert usage.operation_cost(session, identifier).estimated_usd == "0.002910"
        state = usage.budget_status(session, settings)
        assert state.prior_usd == "7.470000" and state.available_usd == "42.527090"
        assert state.reserved_usd == "0.000000"


async def test_exhausted_budget_blocks_before_any_http_request(db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage, "session_factory", lambda: db)
    def must_not_open(**kw: object) -> None:
        raise AssertionError("A blocked call must never open a provider client")
    monkeypatch.setattr("app.integrations.providers.httpx.AsyncClient", must_not_open)
    with pytest.raises(BudgetExceeded):
        await OpenAITransport(configured(ai_budget_prior_spend_usd=Decimal("50"))).post("responses", PAYLOAD)


@pytest.mark.parametrize("failure", ["timeout", "server", "invalid-json"])
async def test_uncertain_billing_keeps_reservation_and_never_replays_the_http_call(db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    monkeypatch.setattr(usage, "session_factory", lambda: db)
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("SYNTHETIC PRIVATE BODY MUST NOT BE LOGGED")
        return httpx.Response(503 if failure == "server" else 200, content=b"not-json")
    client = httpx.AsyncClient
    monkeypatch.setattr("app.integrations.providers.httpx.AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ProviderError, match="automatic retry disabled"):
        await OpenAITransport(configured()).post("responses", PAYLOAD)
    assert calls == 1
    with db() as session:
        row = session.scalar(select(AIUsage))
        assert row and row.status == "uncertain" and row.cost_micro_usd is None
        assert usage.budget_status(session, configured()).reserved_usd == "5.340000"


def test_accounting_survives_business_transaction_rollback_and_restarts(db: sessionmaker[Session]) -> None:
    settings = configured()
    identifier = usage.reserve(settings, "responses", PAYLOAD, factory=db)
    assert identifier
    with pytest.raises(RuntimeError):
        with db.begin():
            usage.finish(identifier, receipt(), factory=db)
            raise RuntimeError("Synthetic extraction validation failed after paid inference")
    with db() as session:
        assert usage.budget_status(session, settings).recorded_usd == "0.002910"
    # Reconstruct settings, as on a restart: baseline is applied once, not again.
    identifier = usage.reserve(configured(), "responses", PAYLOAD, factory=db)
    assert identifier
    with db() as session:
        assert session.scalar(select(func.count()).select_from(AIBudgetPeriod)) == 1
        assert usage.budget_status(session, settings).prior_usd == "7.470000"


def test_month_rollover_does_not_repeat_last_months_manual_baseline(db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage, "clock", lambda: datetime(2026, 11, 30, 23, 59, tzinfo=UTC))
    settings = configured()
    usage.reserve(settings, "responses", PAYLOAD, factory=db)
    monkeypatch.setattr(usage, "clock", lambda: datetime(2026, 12, 1, tzinfo=UTC))
    usage.reserve(settings, "responses", PAYLOAD, factory=db)
    with db() as session:
        assert session.get(AIBudgetPeriod, "2026-11").prior_micro_usd == 7470000
        assert session.get(AIBudgetPeriod, "2026-12").prior_micro_usd == 0


def test_unknown_tariff_or_prior_spend_fail_closed_without_guessing(db: sessionmaker[Session]) -> None:
    with pytest.raises(BudgetConfigurationError, match="verified budget tariff"):
        usage.reserve(configured(), "responses", {**PAYLOAD, "model": "unverified-model"}, factory=db)
    with pytest.raises(BudgetConfigurationError, match="earlier monthly spend"):
        usage.reserve(configured(ai_budget_prior_spend_month=None), "responses", PAYLOAD, factory=db)


def test_long_context_and_unknown_cache_usage_are_conservative() -> None:
    pricing = usage.rate("gpt-6.1-sol")
    assert usage.charge(pricing, usage.Tokens(input=300000, output=1000, cached=0, writes=0)) == 1215000
    assert usage.charge(pricing, usage.Tokens(input=1000, output=200, cached=600)) == 3060
    assert usage.maximum_cost("responses", PAYLOAD) == 5340000


def test_spans_preserve_the_whole_original_once_without_duplicate_context() -> None:
    text = "Início. " + "A" * 6200 + " Decisão final: terça-feira, R$ 42 mil."
    spans = original_spans(text)
    assert "".join(spans) == text and all(0 < len(span) <= 2000 for span in spans)
    assert sum(map(len, spans)) == len(text)


def test_worker_budget_pause_keeps_jobs_pending_without_spending_retry_attempts(db: sessionmaker[Session]) -> None:
    with db.begin() as session:
        job_id = commit_import(session, parse_export(b"06/11/2026, 10:00 - Ana: Eu moro em Recife.", "DMY", "UTC"), "budget.txt", "SYNTHETIC", None).id
    class Blocked(MockLLMProvider):
        async def extract_memories(self, messages):
            raise BudgetExceeded("Synthetic shared budget exhausted")
    for _ in range(2):
        assert not run_once(db, get_settings().model_copy(update={"worker_indexes_messages": False}), Blocked(), MockEmbeddingProvider(256))
    with db() as session:
        job = session.get(ImportJob, job_id)
        assert job and job.status == "pending" and job.attempts == 0 and job.processed_messages == 0
        assert session.scalar(select(func.count()).select_from(Message)) == 1


def test_uncertain_background_usage_pauses_automatic_replay_and_stale_caps_fail_closed(db: sessionmaker[Session]) -> None:
    settings = configured(ai_usage_role="indexing")
    payload = {"model": "text-embedding-3-small"}
    identifier = usage.reserve(settings, "embeddings", payload, factory=db)
    usage.finish(identifier, factory=db)
    with pytest.raises(BudgetExceeded):
        usage.reserve(settings, "embeddings", payload, factory=db)
    with pytest.raises(BudgetConfigurationError):
        usage.reserve(settings.model_copy(update={"ai_monthly_budget_usd": Decimal("100")}), "embeddings", payload, factory=db)
    # A user can deliberately make another query; the unknown cost stays held.
    assert usage.reserve(settings.model_copy(update={"ai_usage_role": "queries"}), "responses", PAYLOAD, factory=db)


def test_usage_endpoint_requires_auth_and_never_calls_a_provider(db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app
    settings = configured()
    monkeypatch.setattr("app.api.routes.v1.get_settings", lambda: settings)
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/settings/usage").status_code == 401
        result = client.get("/api/v1/settings/usage", headers={"Authorization": "Bearer " + settings.auth_secret.get_secret_value()})
        assert result.status_code == 200
        assert result.json()["available_usd"] == "42.530000"
        assert result.json()["calls"] == 0


def test_exhausted_budget_returns_actionable_api_error_without_opening_provider(db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app
    settings = configured(llm_provider="openai", embedding_provider="openai", ai_budget_prior_spend_usd=Decimal("50"))
    monkeypatch.setattr("app.api.routes.v1.runtime_settings", lambda session: settings)
    monkeypatch.setattr(usage, "session_factory", lambda: db)
    def must_not_open(**kw: object) -> None:
        raise AssertionError("A blocked query must never open a provider client")
    monkeypatch.setattr("app.integrations.providers.httpx.AsyncClient", must_not_open)
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/assistant/query", json={"question": "Qual foi o prazo no projeto sintético?"}, headers={"Authorization": "Bearer " + settings.auth_secret.get_secret_value()})
        assert response.status_code == 402
        assert "captura do WhatsApp continua" in response.json()["error"]["message"]
    with db() as session:
        assert session.scalar(select(func.count()).select_from(AIUsage)) == 0


async def test_rejected_provider_request_releases_its_reservation(db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage, "session_factory", lambda: db)
    client = httpx.AsyncClient
    monkeypatch.setattr("app.integrations.providers.httpx.AsyncClient", lambda **kw: client(transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {"message": "Synthetic rejection"}})), **kw))
    with pytest.raises(ProviderError, match="chave inválida"):
        await OpenAITransport(configured()).post("responses", PAYLOAD)
    with db() as session:
        assert session.scalar(select(AIUsage)).status == "rejected"
        state = usage.budget_status(session, configured())
        assert state.available_usd == "42.530000" and state.reserved_usd == "0.000000"


async def test_indexer_owns_original_vectors_while_worker_still_extracts_memories(db: sessionmaker[Session]) -> None:
    with db.begin() as session:
        commit_import(session, parse_export(b"06/11/2026, 10:00 - Ana: Eu moro em Recife.", "DMY", "UTC"), "ownership.txt", "SYNTHETIC", None)
    settings = get_settings().model_copy(update={"worker_indexes_messages": False})
    assert await asyncio.to_thread(run_once, db, settings, MockLLMProvider(), MockEmbeddingProvider(256))
    with db() as session:
        assert session.scalar(select(func.count()).select_from(MessageEmbedding)) == 0
        assert session.scalar(select(func.count()).select_from(Memory)) == 1
    assert await index_once(db, settings, MockEmbeddingProvider(256)) == 1
    assert await index_once(db, settings, MockEmbeddingProvider(256)) == 0
