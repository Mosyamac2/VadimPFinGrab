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
    # Patch 33: when non-null, the provider must send only these
    # zero-based pages from ``pdf_bytes``. Other pages are dropped.
    # ``None`` keeps the legacy "send the whole document" behaviour.
    # Used by the Metric Extractor's vision-fallback retry to point
    # Anthropic at just the scan pages instead of the whole report.
    pdf_page_indices: tuple[int, ...] | None = None
    # Patch 34: pre-rendered page images (PNG bytes). When set, the
    # provider must send each item as an Anthropic ``image`` content
    # block in the same order. Takes precedence over ``pdf_bytes`` /
    # ``pdf_page_indices`` — explicit per-ticker opt-in for the
    # full-vision path on issuers where text + native PDF both fail.
    pdf_page_images: tuple[bytes, ...] | None = None
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
    # Patch 28: prompt-caching observability. ``cache_read_input_tokens``
    # are tokens served from cache (billed at 0.1× and not counted toward
    # the org's ITPM rate limit). ``cache_creation_input_tokens`` are the
    # tokens written *into* cache by this request (billed at 1.25×;
    # subsequent calls within the TTL pay 0.1×). Sum of all four
    # ``*_input_tokens`` ≤ true input tokens for the request — they're
    # disjoint buckets.
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


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
