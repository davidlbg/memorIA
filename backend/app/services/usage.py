"""Content-free accounting. Reservations commit BEFORE any billable HTTP call.

Use documented worst-case model limits rather than guessing token counts. This
can refuse a near-limit request early, but avoids a paid counting call or an
unproven tokenizer estimate. Reservations never expire automatically: a process
can die after a provider accepted its request. Accounting uses separate short DB
transactions so a failed extraction/edited source cannot roll back its cost.
"""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import BigInteger, case, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database.session import session_factory
from app.integrations.errors import BudgetConfigurationError, BudgetExceeded
from app.models.entities import AIBudgetPeriod, AIUsage
from app.schemas.usage import BudgetStatus, QueryCost

logger = logging.getLogger("memory.usage")
operation: ContextVar[UUID | None] = ContextVar("ai_usage_operation", default=None)
MICRO = Decimal(1000000)


def clock() -> datetime:
    return datetime.now(UTC)


def micros(usd: Decimal) -> int:
    return int((usd * MICRO).to_integral_value(rounding=ROUND_CEILING))


def dollars(value: int) -> str:
    return f"{Decimal(value) / MICRO:.6f}"


@contextmanager
def usage_operation() -> Iterator[UUID]:
    identifier = uuid4()
    token = operation.set(identifier)
    try:
        yield identifier
    finally:
        operation.reset(token)


@dataclass(frozen=True)
class Rate:
    name: str
    input: Decimal  # micro-USD per token == USD per million tokens
    cached: Decimal
    write: Decimal
    output: Decimal
    context: int
    long_context: bool = False


def rate(model: str) -> Rate:
    if model == "gpt-6.1-sol":
        return Rate(model, Decimal("2"), Decimal("0.1"), Decimal("2.5"), Decimal("10"), 1050000, True)
    if re.fullmatch(r"gpt-4\.1-mini(?:-\d{4}-\d{2}-\d{2})?", model):
        return Rate("gpt-4.1-mini", Decimal("0.4"), Decimal("0.1"), Decimal("0.4"), Decimal("1.6"), 1050000)
    if model == "text-embedding-3-small":
        return Rate(model, Decimal("0.02"), Decimal("0.02"), Decimal("0.02"), Decimal(0), 300000)
    raise BudgetConfigurationError("Model has no verified budget tariff")


def maximum_cost(endpoint: str, payload: dict[str, object]) -> int:
    model = payload.get("model")
    if not isinstance(model, str):
        raise BudgetConfigurationError("Model has no verified budget tariff")
    pricing = rate(model)
    if endpoint == "embeddings" and pricing.name == "text-embedding-3-small":
        return int((pricing.input * pricing.context).to_integral_value(rounding=ROUND_CEILING))
    if endpoint not in {"responses", "chat/completions"} or pricing.name == "text-embedding-3-small":
        raise BudgetConfigurationError("Endpoint has no verified budget tariff")
    output = payload.get("max_output_tokens", payload.get("max_completion_tokens"))
    if not isinstance(output, int) or isinstance(output, bool) or not 0 < output <= 128000:
        raise BudgetConfigurationError("Paid generation requires an explicit output limit")
    # Sol >272K inputs: double all input/cache rates, 1.5x output. We explicitly
    # request standard service and 30m cache, so no Fast/Ultrafast/12h surcharge.
    input_multiplier = Decimal(2) if pricing.long_context else Decimal(1)
    output_multiplier = Decimal("1.5") if pricing.long_context else Decimal(1)
    ceiling = max(pricing.input, pricing.write) * pricing.context * input_multiplier + pricing.output * output * output_multiplier
    return int(ceiling.to_integral_value(rounding=ROUND_CEILING))


def prior_for(settings: Settings, month: str) -> int | None:
    if settings.ai_budget_prior_spend_month == month:
        return micros(settings.ai_budget_prior_spend_usd)
    if settings.ai_budget_prior_spend_month and settings.ai_budget_prior_spend_month < month:
        return 0
    return None


class Amounts(BaseModel):
    spent: int = Field(ge=0, strict=True)
    held: int = Field(ge=0, strict=True)


def totals(session: Session, month: str) -> tuple[int, int]:
    # One statement snapshot is essential: two SUM statements could straddle a
    # settlement and miss both the old reservation and the new recorded cost.
    amounts = Amounts.model_validate(dict(session.execute(select(
        cast(func.coalesce(func.sum(case((AIUsage.status == "settled", AIUsage.cost_micro_usd), else_=0)), 0), BigInteger).label("spent"),
        cast(func.coalesce(func.sum(case((AIUsage.status.in_(["reserved", "uncertain"]), AIUsage.reserved_micro_usd), else_=0)), 0), BigInteger).label("held"),
    ).where(AIUsage.month == month)).mappings().one()))
    return amounts.spent, amounts.held


def reserve(settings: Settings, endpoint: str, payload: dict[str, object], *, factory: sessionmaker[Session] | None = None) -> UUID | None:
    if settings.ai_monthly_budget_usd is None:
        return None
    ceiling = maximum_cost(endpoint, payload)
    instant = clock()
    month = instant.strftime("%Y-%m")
    model = payload.get("model")
    if not isinstance(model, str):
        raise BudgetConfigurationError("Model has no verified budget tariff")
    with (factory or session_factory()).begin() as session:
        session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("ai-budget:" + month, 0))))
        session.execute(insert(AIBudgetPeriod).values(month=month, limit_micro_usd=micros(settings.ai_monthly_budget_usd), prior_micro_usd=prior_for(settings, month), started_at=instant).on_conflict_do_nothing())
        period = session.get(AIBudgetPeriod, month)
        if period is None or period.prior_micro_usd is None:
            raise BudgetConfigurationError("Budget needs the earlier monthly spend")
        if period.limit_micro_usd != micros(settings.ai_monthly_budget_usd):
            raise BudgetConfigurationError("Budget configuration differs from the shared period")
        # The DB period is canonical across users, API keys and processes. A
        # stale worker configuration cannot raise the shared limit.
        spent, held = totals(session, month)
        if settings.ai_usage_role in {"memories", "indexing"} and session.scalar(select(AIUsage.id).where(AIUsage.month == month, AIUsage.purpose == settings.ai_usage_role, AIUsage.status == "uncertain").limit(1)):
            raise BudgetExceeded("Background billing needs confirmation before automatic processing resumes")
        if period.prior_micro_usd + spent + held + ceiling > period.limit_micro_usd:
            raise BudgetExceeded("Shared monthly AI budget cannot reserve this call")
        record = AIUsage(created_at=instant, month=month, operation_id=operation.get() or uuid4(), purpose=settings.ai_usage_role, model_name=model, endpoint=endpoint, reserved_micro_usd=ceiling, status="reserved")
        session.add(record)
        session.flush()
        identifier = record.id
    return identifier


class Tokens(BaseModel):
    input: int = Field(ge=0, strict=True)
    output: int = Field(ge=0, strict=True)
    cached: int | None = Field(default=None, ge=0, strict=True)
    writes: int | None = Field(default=None, ge=0, strict=True)
    reasoning: int | None = Field(default=None, ge=0, strict=True)


def usage_tokens(endpoint: str, body: dict[str, object]) -> Tokens:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Missing usage")
    if endpoint == "embeddings":
        values = {"input": usage.get("prompt_tokens"), "output": 0}
    elif endpoint == "responses":
        details = usage.get("input_tokens_details")
        output_details = usage.get("output_tokens_details")
        values = {"input": usage.get("input_tokens"), "output": usage.get("output_tokens"), "cached": details.get("cached_tokens") if isinstance(details, dict) else None, "writes": details.get("cache_write_tokens") if isinstance(details, dict) else None, "reasoning": output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None}
        if isinstance(details, dict):
            writes = details.get("cache_write_tokens_details")
            if isinstance(writes, dict) and writes.get("ttl_12h_tokens", 0) != 0:
                raise ValueError("Unexpected cache lifetime")
    elif endpoint == "chat/completions":
        details = usage.get("prompt_tokens_details")
        output_details = usage.get("completion_tokens_details")
        values = {"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens"), "cached": details.get("cached_tokens") if isinstance(details, dict) else None, "writes": 0, "reasoning": output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None}
    else:
        raise ValueError("Unsupported usage")
    result = Tokens.model_validate(values)
    total = usage.get("total_tokens")
    if total is not None and (not isinstance(total, int) or isinstance(total, bool) or total != result.input + result.output):
        raise ValueError("Inconsistent total usage")
    if (result.cached or 0) + (result.writes or 0) > result.input or (result.reasoning or 0) > result.output:
        raise ValueError("Inconsistent usage")
    return result


def charge(pricing: Rate, tokens: Tokens) -> int:
    cached, writes = tokens.cached or 0, tokens.writes or 0
    ordinary = tokens.input - cached - writes
    # An omitted cache-write count is UNKNOWN, not zero. Charge a conservative
    # upper estimate for the remaining inputs, rather than under-report cost.
    ordinary_rate = max(pricing.input, pricing.write) if tokens.writes is None else pricing.input
    input_multiplier = Decimal(2) if pricing.long_context and tokens.input > 272000 else Decimal(1)
    output_multiplier = Decimal("1.5") if pricing.long_context and tokens.input > 272000 else Decimal(1)
    cost = (ordinary * ordinary_rate + cached * pricing.cached + writes * pricing.write) * input_multiplier + tokens.output * pricing.output * output_multiplier
    return int(cost.to_integral_value(rounding=ROUND_CEILING))


def finish(identifier: UUID | None, body: dict[str, object] | None = None, *, rejected: bool = False, factory: sessionmaker[Session] | None = None) -> None:
    if identifier is None:
        return
    with (factory or session_factory()).begin() as session:
        record = session.scalar(select(AIUsage).where(AIUsage.id == identifier).with_for_update())
        if record is None:
            raise BudgetConfigurationError("Budget reservation disappeared")
        if record.status != "reserved":
            raise BudgetConfigurationError("Budget reservation was already finalized")
        record.status = "rejected" if rejected else "uncertain"
        if rejected:
            return
        try:
            if body is None or body.get("service_tier") not in {None, "default", "standard"}:
                raise ValueError("Unconfirmed billing")
            actual_model = body.get("model")
            if not isinstance(actual_model, str) or rate(actual_model).name != rate(record.model_name).name:
                raise ValueError("Unexpected billing model")
            tokens = usage_tokens(record.endpoint, body)
            cost = charge(rate(actual_model), tokens)
            if cost > record.reserved_micro_usd:
                raise ValueError("Usage exceeds reserved ceiling")
        except (ValueError, ValidationError, BudgetConfigurationError):
            logger.warning("Provider billing unconfirmed; conservative reservation retained (no content logged)")
            return
        record.status, record.cost_micro_usd = "settled", cost
        record.input_tokens, record.output_tokens = tokens.input, tokens.output
        record.cached_tokens, record.cache_write_tokens, record.reasoning_tokens = tokens.cached, tokens.writes, tokens.reasoning


def operation_cost(session: Session, identifier: UUID) -> QueryCost | None:
    rows = list(session.scalars(select(AIUsage).where(AIUsage.operation_id == identifier)))
    if not rows:
        return None
    unknown = sum(row.status in {"uncertain", "reserved"} for row in rows)
    return QueryCost(estimated_usd=None if unknown else dollars(sum(row.cost_micro_usd or 0 for row in rows)), calls=len(rows), unconfirmed_calls=unknown)


def budget_status(session: Session, settings: Settings) -> BudgetStatus:
    month = clock().strftime("%Y-%m")
    period = session.get(AIBudgetPeriod, month)
    prior = period.prior_micro_usd if period else prior_for(settings, month)
    limit = period.limit_micro_usd if period else (micros(settings.ai_monthly_budget_usd) if settings.ai_monthly_budget_usd is not None else None)
    spent, held = totals(session, month)
    rows = list(session.scalars(select(AIUsage).where(AIUsage.month == month)))
    breakdown: dict[str, int] = {}
    for row in rows:
        if row.status == "settled":
            breakdown[row.purpose] = breakdown.get(row.purpose, 0) + (row.cost_micro_usd or 0)
    try:
        query_ceiling = maximum_cost("responses", {"model": settings.openai_query_model or settings.llm_model, "max_output_tokens": 6000})
    except BudgetConfigurationError:
        query_ceiling = None  # Explicitly unavailable tariff, never a guessed price.
    return BudgetStatus(enabled=settings.ai_monthly_budget_usd is not None, month=month, limit_usd=dollars(limit) if limit is not None else None, prior_usd=dollars(prior) if prior is not None else None, recorded_usd=dollars(spent), reserved_usd=dollars(held), available_usd=dollars(max(0, limit - (prior or 0) - spent - held)) if limit is not None and prior is not None else None, query_reservation_usd=dollars(query_ceiling) if query_ceiling is not None else None, baseline_known=prior is not None, started_at=period.started_at if period else None, calls=len(rows), unconfirmed_calls=sum(row.status == "uncertain" for row in rows), by_purpose={name: dollars(value) for name, value in breakdown.items()})
