"""Direct Anthropic API provider with tool-use for strict JSON output."""

from __future__ import annotations

import base64
import time
from typing import Any

import anthropic
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
)

from edx.logging_setup import get_logger
from edx.providers.llm._retry import build_llm_wait
from edx.providers.llm.base import (
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
)

_RETRYABLE_ANTHROPIC_EXC: tuple[type[BaseException], ...] = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)


class AnthropicLLMProvider:
    """Anthropic Claude via the official SDK.

    Strict JSON is achieved via tool-use with a single tool whose
    ``input_schema`` is ``req.json_schema``. PDF inputs go through the native
    ``document`` content block; ``cache_control`` is set on the system prompt
    so large static instructions hit the prompt cache (5-minute TTL).
    """

    name = "anthropic"
    supports_pdf_input = True

    def __init__(
        self,
        *,
        client: anthropic.AsyncAnthropic,
        model: str,
        max_retries: int = 3,
        retry_min_wait_s: float = 0.5,
        retry_max_wait_s: float = 10.0,
        enable_prompt_caching: bool = True,
        # Patch 28: bump the prompt-cache TTL from the 5-minute default
        # to 1 hour. Two practical benefits:
        # 1. A single ``edx run --full-reload`` can take 15–30 min on a
        #    Top-50 ticker list; with 5-min TTL the cache evaporates
        #    mid-run for slow tickers.
        # 2. Daily cron jobs that fire every 24h still warm-start the
        #    cache for the first ~1h of the next run if the operator
        #    does a manual ``edx update`` shortly after the cron.
        # Pass ``cache_ttl="5m"`` to revert to the original behaviour.
        cache_ttl: str = "1h",
    ) -> None:
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self.retry_min_wait_s = retry_min_wait_s
        self.retry_max_wait_s = retry_max_wait_s
        self.enable_prompt_caching = enable_prompt_caching
        self.cache_ttl = cache_ttl
        self._log = get_logger("edx.providers.llm.anthropic")

    @classmethod
    def create(
        cls,
        *,
        api_key: str,
        model: str,
        request_timeout_s: float = 120.0,
        max_retries: int = 3,
        retry_min_wait_s: float = 0.5,
        retry_max_wait_s: float = 10.0,
        enable_prompt_caching: bool = True,
        cache_ttl: str = "1h",
    ) -> AnthropicLLMProvider:
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=request_timeout_s,
            max_retries=0,  # we manage retries ourselves via tenacity
        )
        return cls(
            client=client,
            model=model,
            max_retries=max_retries,
            retry_min_wait_s=retry_min_wait_s,
            retry_max_wait_s=retry_max_wait_s,
            enable_prompt_caching=enable_prompt_caching,
            cache_ttl=cache_ttl,
        )

    async def complete(self, req: LLMRequest) -> LLMResponse:
        system_blocks = self._build_system_blocks(req.system)
        user_content = self._build_user_content(req)
        tool_def = {
            "name": req.schema_name,
            "description": req.schema_description,
            "input_schema": req.json_schema,
        }

        async def _attempt() -> LLMResponse:
            t0 = time.monotonic()
            try:
                response = await self.client.messages.create(  # type: ignore[call-overload]
                    model=self.model,
                    max_tokens=req.max_tokens,
                    temperature=req.temperature,
                    system=system_blocks,
                    messages=[{"role": "user", "content": user_content}],
                    tools=[tool_def],
                    tool_choice={"type": "tool", "name": req.schema_name},
                )
            except (
                anthropic.AuthenticationError,
                anthropic.PermissionDeniedError,
            ) as exc:
                raise LLMUnavailableError(
                    f"anthropic auth: {exc}"
                ) from exc
            except anthropic.BadRequestError as exc:
                raise LLMUnavailableError(
                    f"anthropic bad request: {exc}"
                ) from exc
            except anthropic.APIStatusError as exc:
                if exc.status_code == 402:
                    raise LLMUnavailableError(
                        f"anthropic billing: {exc}"
                    ) from exc
                raise

            elapsed = time.monotonic() - t0
            data = self._extract_tool_use(response, req.schema_name)
            usage = getattr(response, "usage", None)
            in_tokens = int(getattr(usage, "input_tokens", 0)) if usage else 0
            out_tokens = int(getattr(usage, "output_tokens", 0)) if usage else 0
            # Patch 28: surface prompt-cache fields from the Anthropic
            # usage response. ``cache_read_input_tokens`` is the slice
            # served from cache (0.1× billed, doesn't count toward
            # ITPM). ``cache_creation_input_tokens`` is the slice
            # written *into* cache by this request (1.25× billed).
            cache_read_tokens = (
                int(getattr(usage, "cache_read_input_tokens", 0) or 0)
                if usage
                else 0
            )
            cache_creation_tokens = (
                int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
                if usage
                else 0
            )

            self._log.info(
                "llm_request_completed",
                provider=self.name,
                model=self.model,
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                cache_read_input_tokens=cache_read_tokens,
                cache_creation_input_tokens=cache_creation_tokens,
                elapsed_s=round(elapsed, 4),
                status="success",
            )
            return LLMResponse(
                data=data,
                raw_text=_safe_repr(data),
                provider=self.name,
                model=self.model,
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                cache_read_input_tokens=cache_read_tokens,
                cache_creation_input_tokens=cache_creation_tokens,
            )

        retrier: AsyncRetrying = AsyncRetrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=build_llm_wait(self.retry_min_wait_s, self.retry_max_wait_s),
            retry=retry_if_exception_type(_RETRYABLE_ANTHROPIC_EXC),
            reraise=True,
        )
        try:
            return await retrier(_attempt)
        except _RETRYABLE_ANTHROPIC_EXC as exc:
            self._log.warning(
                "llm_request_completed",
                provider=self.name,
                model=self.model,
                status="failed",
                error=str(exc),
            )
            raise LLMUnavailableError(
                f"anthropic exhausted retries: {exc}"
            ) from exc

    def _build_system_blocks(self, text: str) -> list[dict[str, Any]]:
        block: dict[str, Any] = {"type": "text", "text": text}
        if self.enable_prompt_caching:
            cache_control: dict[str, Any] = {"type": "ephemeral"}
            # The Anthropic API treats absent ``ttl`` as "5m". Only set
            # the field for the 1h variant — that keeps generated request
            # bodies identical to the pre-Patch-28 wire format whenever
            # the operator hasn't opted into the longer TTL.
            if self.cache_ttl and self.cache_ttl != "5m":
                cache_control["ttl"] = self.cache_ttl
            block["cache_control"] = cache_control
        return [block]

    def _build_user_content(self, req: LLMRequest) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        if req.pdf_bytes is not None:
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(req.pdf_bytes).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": req.user_text})
        return content

    def _extract_tool_use(
        self, response: Any, schema_name: str
    ) -> dict[str, Any]:
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "tool_use":
                if getattr(block, "name", None) != schema_name:
                    continue
                payload = getattr(block, "input", None)
                if isinstance(payload, dict):
                    return payload
        raise LLMUnavailableError(
            "anthropic response did not contain a tool_use block "
            f"with name={schema_name!r}"
        )


def _safe_repr(data: Any) -> str:
    try:
        import json

        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(data)
