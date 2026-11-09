"""Typed provider boundary for the read-only research loop."""

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ResearchFunction(BaseModel):
    name: str
    arguments: str


class ResearchCall(BaseModel):
    id: str
    type: Literal["function"]
    function: ResearchFunction


class ResearchTurn(BaseModel):
    role: Literal["assistant"]
    content: str | None = None
    tool_calls: list[ResearchCall] | None = Field(default=None, max_length=1)
    refusal: str | None = None


@runtime_checkable
class ResearchClient(Protocol):
    async def research_step(
        self, messages: list[dict[str, object]], tools: list[dict[str, object]]
    ) -> ResearchTurn: ...


@runtime_checkable
class DirectAnswerClient(ResearchClient, Protocol):
    supports_grounded_answers: bool
