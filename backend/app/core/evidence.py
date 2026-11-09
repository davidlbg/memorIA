"""Finite original quote references for a directly grounded research answer."""

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.domain import Claim, GeneratedAnswer, SourceMessage, SupportingQuote


def original_spans(content: str) -> list[str]:
    if len(content) <= 2000:
        return [content]
    # Preserve every character once. A claim can select multiple original spans;
    # repeating half of each long message wastes paid context without new facts.
    return [content[start:start + 2000] for start in range(0, len(content), 2000)]


class EvidenceParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    quote_refs: list[Annotated[str, Field(pattern=r"^m[1-9][0-9]*:q[1-9][0-9]*$")]] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def distinct_quotes(self) -> "EvidenceParagraph":
        if len(self.quote_refs) != len(set(self.quote_refs)):
            raise ValueError("Each original quote may be selected once per paragraph")
        return self


class ReplyClaim(EvidenceParagraph):
    section: str | None = Field(default=None, min_length=1, max_length=80)


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_mode: Literal["overview"]
    claims: list[ReplyClaim] = Field(max_length=10)


class FocusedReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_mode: Literal["focused"]
    temporal_scope: Literal["current", "historical", "not_time_sensitive"]
    claims: list[EvidenceParagraph] = Field(max_length=4)


@dataclass(frozen=True)
class OriginalQuote:
    source: SourceMessage
    text: str


class QuoteRegistry:
    def __init__(self) -> None:
        self.quotes: dict[str, OriginalQuote] = {}

    def register(self, message_ref: str, source: SourceMessage) -> list[dict[str, str]]:
        options = []
        for index, text in enumerate(original_spans(source.content), 1):
            ref = f"{message_ref}:q{index}"
            self.quotes[ref] = OriginalQuote(source, text)
            options.append({"quote_ref": ref, "text": text})
        return options

    def answer(self, reply: Reply | FocusedReply) -> tuple[GeneratedAnswer, list[SourceMessage]]:
        claims = []
        sources: dict[UUID, SourceMessage] = {}
        for item in reply.claims:
            if any(ref not in self.quotes for ref in item.quote_refs):
                raise ValueError("Answer selected a quote not returned by the data API")
            originals = [self.quotes[ref] for ref in item.quote_refs]
            # A source may support multiple distinct original spans. Its ID
            # occurs once in source_message_ids; all chosen spans are preserved.
            message_ids = list(dict.fromkeys(quote.source.id for quote in originals))
            if len(message_ids) > 10:
                raise ValueError("Split a paragraph requiring more than ten original messages")
            claims.append(Claim(kind="interpretation", section=item.section if isinstance(item, ReplyClaim) else None, text=item.text,
                                source_message_ids=message_ids, quote=originals[0].text,
                                supporting_quotes=[SupportingQuote(message_id=quote.source.id, quote=quote.text) for quote in originals]))
            for quote in originals:
                sources.setdefault(quote.source.id, quote.source)
        return GeneratedAnswer(claims=claims), list(sources.values())
