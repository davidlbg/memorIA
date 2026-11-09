"""Stateless Responses transport: replay opaque reasoning, never log or persist it."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError

from app.config import Settings
from app.integrations.providers import OpenAITransport, ProviderError, strict_openai_schema
from app.integrations.research import ResearchCall, ResearchFunction, ResearchTurn


class Response(BaseModel):
    status: Literal["completed"]
    output: list[dict[str, JsonValue]]


class FunctionCall(BaseModel):
    type: Literal["function_call"]
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: str


class Tool(BaseModel):
    type: Literal["function"]
    function: dict[str, JsonValue]


class TextPart(BaseModel):
    type: Literal["output_text"]
    text: str


class OutputMessage(BaseModel):
    type: Literal["message"]
    role: Literal["assistant"]
    content: list[TextPart] = Field(min_length=1, max_length=1)


@dataclass
class ResearchState:
    items: list[dict[str, JsonValue]] = field(default_factory=list)
    pending_call: str | None = None


async def research_step(transport: OpenAITransport, settings: Settings, state: ResearchState, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ResearchTurn:
    try:
        instructions = messages[0].get("content")
        if not isinstance(instructions, str):
            raise ValueError("Missing research instructions")
        if len(messages) == 2:
            state.items = TypeAdapter(list[dict[str, JsonValue]]).validate_python([messages[1]])
            state.pending_call = None
        else:
            result = messages[-1]
            if result.get("role") != "tool" or result.get("tool_call_id") != state.pending_call or not isinstance(result.get("content"), str):
                raise ValueError("Mismatched research result")
            state.items.append(TypeAdapter(dict[str, JsonValue]).validate_python({"type": "function_call_output", "call_id": state.pending_call, "output": result["content"]}))
        functions = [{"type": "function", **Tool.model_validate(tool).function} for tool in tools]
        raw = await transport.post("responses", {
            "model": settings.llm_model, "instructions": instructions,
            "input": state.items, "tools": functions, "tool_choice": "required",
            "parallel_tool_calls": False, "store": False,
            "reasoning": {"effort": settings.openai_reasoning_effort},
            "include": ["reasoning.encrypted_content"], "max_output_tokens": 6000,
        })
        response = Response.model_validate(raw)
        calls = []
        for item in response.output:
            if item.get("type") == "function_call":
                calls.append(FunctionCall.model_validate(item))
            elif item.get("type") != "reasoning" or not isinstance(item.get("encrypted_content"), str):
                raise ValueError("Unexpected research output")
        if len(calls) != 1:
            raise ValueError("Research requires one function call")
        # Keep every output item, including encrypted reasoning, for the next
        # tool result. No response IDs or server-side conversation storage.
        state.items.extend(response.output)
        state.pending_call = calls[0].call_id
        return ResearchTurn(role="assistant", tool_calls=[ResearchCall(id=calls[0].call_id, type="function", function=ResearchFunction(name=calls[0].name, arguments=calls[0].arguments))])
    except (ValidationError, ValueError, IndexError):
        raise ProviderError("OpenAI Responses returned invalid or incomplete research") from None


async def complete(transport: OpenAITransport, settings: Settings, messages: list[dict[str, str]], decoder: dict[str, object]) -> str:
    raw = await transport.post("responses", {
        "model": settings.llm_model, "input": messages, "store": False,
        "reasoning": {"effort": settings.openai_reasoning_effort},
        "text": {"format": {"type": "json_schema", "name": "GroundedEvidence", "strict": True, "schema": strict_openai_schema(decoder)}},
        "max_output_tokens": 8000,
    })
    try:
        response = Response.model_validate(raw)
        answers = [OutputMessage.model_validate(item) for item in response.output if item.get("type") == "message"]
        if len(answers) != 1 or any(item.get("type") not in {"message", "reasoning"} for item in response.output):
            raise ValueError("Unexpected answer output")
        return answers[0].content[0].text
    except (ValidationError, ValueError):
        raise ProviderError("OpenAI Responses generation incomplete, refused or invalid") from None
