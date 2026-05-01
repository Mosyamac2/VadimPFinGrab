"""Common interface and value types for LLM providers."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class LLMRequest(BaseModel):
    """One LLM call: system + user content + strict output schema."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    system: str
    user_text: str
    pdf_bytes: bytes | None = None
    json_schema: dict[str, Any]
    max_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0.0, le=2.0)
    schema_name: str = "extraction"
    schema_description: str = "Return the structured extraction result."


class LLMResponse(BaseModel):
    """Result of one LLM call."""

    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any]
    raw_text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMUnavailableError(RuntimeError):
    """Raised when a provider can't fulfil a request and the chain should move on.

    Used both for hard auth/credit failures and for retries-exhausted transient
    failures. The :class:`FallbackChain` listens for this exception type to
    route to the next provider.
    """


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol every LLM provider must implement."""

    name: str
    supports_pdf_input: bool

    async def complete(self, req: LLMRequest) -> LLMResponse: ...
