from datetime import datetime

from pydantic import BaseModel


class QueryCost(BaseModel):
    estimated_usd: str | None
    calls: int
    unconfirmed_calls: int


class BudgetStatus(BaseModel):
    enabled: bool
    month: str
    limit_usd: str | None
    prior_usd: str | None
    recorded_usd: str
    reserved_usd: str
    available_usd: str | None
    query_reservation_usd: str | None
    baseline_known: bool
    started_at: datetime | None
    calls: int
    unconfirmed_calls: int
    by_purpose: dict[str, str]
    pricing_version: str = "openai-standard-2026-11-06"
