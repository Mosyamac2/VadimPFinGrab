"""Fallback chain that tries providers in order until one succeeds."""

from __future__ import annotations

import httpx

from edx.logging_setup import get_logger
from edx.providers.llm.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
)


class FallbackChain:
    """Try providers in order; on ``LLMUnavailableError`` move to the next.

    Network ``httpx.TransportError`` is also treated as fallback-worthy: a
    misbehaving primary should not block the run if the secondary works.
    """

    name = "chain"

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("FallbackChain requires at least one provider")
        self.providers = providers
        self.supports_pdf_input = providers[0].supports_pdf_input
        self._log = get_logger("edx.providers.llm.chain")

    async def complete(self, req: LLMRequest) -> LLMResponse:
        last_error: BaseException | None = None
        for index, provider in enumerate(self.providers):
            try:
                return await provider.complete(req)
            except (LLMUnavailableError, httpx.TransportError) as exc:
                last_error = exc
                next_provider = (
                    self.providers[index + 1].name
                    if index + 1 < len(self.providers)
                    else None
                )
                if next_provider is not None:
                    self._log.warning(
                        "llm_fallback",
                        from_provider=provider.name,
                        to_provider=next_provider,
                        error=str(exc),
                    )
                    continue
                self._log.error(
                    "llm_chain_exhausted",
                    last_provider=provider.name,
                    error=str(exc),
                )
                if isinstance(exc, LLMUnavailableError):
                    raise
                raise LLMUnavailableError(
                    f"chain exhausted: {exc}"
                ) from exc

        # Unreachable in practice (loop always returns or raises); kept for type-checkers.
        raise LLMUnavailableError(  # pragma: no cover
            f"chain exhausted: {last_error}"
        )
