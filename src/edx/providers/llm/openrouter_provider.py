"""OpenRouter fallback provider via raw HTTP."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from json_repair import repair_json
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


class _OpenRouterRetryableHTTPError(Exception):
    """Internal marker driving tenacity retry on retryable HTTP statuses."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(f"retryable HTTP status {response.status_code}")


_RETRYABLE_EXC: tuple[type[BaseException], ...] = (
    httpx.TransportError,
    _OpenRouterRetryableHTTPError,
)
_NON_RETRYABLE_AUTH_STATUSES: frozenset[int] = frozenset(
    {400, 401, 402, 403, 404, 422}
)


class OpenRouterLLMProvider:
    """OpenRouter (https://openrouter.ai) HTTP client.

    Uses ``response_format=json_schema`` when supported; on parse failure or
    text fallback, runs the response through ``json-repair`` before raising.
    PDF inputs are not natively supported and are dropped with a warning —
    callers (i.e. the extractor stage) are responsible for sending text-only
    bodies to providers without PDF support.
    """

    name = "openrouter"
    supports_pdf_input = False

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str,
        model: str,
        max_retries: int = 3,
        retry_min_wait_s: float = 0.5,
        retry_max_wait_s: float = 10.0,
    ) -> None:
        self.client = client
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.retry_min_wait_s = retry_min_wait_s
        self.retry_max_wait_s = retry_max_wait_s
        self._log = get_logger("edx.providers.llm.openrouter")

    @classmethod
    def create(
        cls,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        request_timeout_s: float = 120.0,
        max_retries: int = 3,
        retry_min_wait_s: float = 0.5,
        retry_max_wait_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> OpenRouterLLMProvider:
        client = httpx.AsyncClient(
            base_url=base_url,
            timeout=request_timeout_s,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        return cls(
            client=client,
            api_key=api_key,
            model=model,
            max_retries=max_retries,
            retry_min_wait_s=retry_min_wait_s,
            retry_max_wait_s=retry_max_wait_s,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if req.pdf_bytes is not None:
            self._log.warning(
                "openrouter_pdf_input_dropped",
                bytes=len(req.pdf_bytes),
            )

        body = self._build_body(req)

        async def _attempt() -> httpx.Response:
            response = await self.client.post("/chat/completions", json=body)
            if response.status_code in _NON_RETRYABLE_AUTH_STATUSES:
                detail = response.text[:300]
                raise LLMUnavailableError(
                    f"openrouter HTTP {response.status_code}: {detail}"
                )
            if response.status_code >= 500 or response.status_code == 429:
                raise _OpenRouterRetryableHTTPError(response)
            if response.status_code != 200:
                raise LLMUnavailableError(
                    f"openrouter HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )
            return response

        retrier: AsyncRetrying = AsyncRetrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=build_llm_wait(self.retry_min_wait_s, self.retry_max_wait_s),
            retry=retry_if_exception_type(_RETRYABLE_EXC),
            reraise=True,
        )

        t0 = time.monotonic()
        try:
            response: httpx.Response = await retrier(_attempt)
        except _OpenRouterRetryableHTTPError as exc:
            raise LLMUnavailableError(
                f"openrouter exhausted retries (HTTP "
                f"{exc.response.status_code})"
            ) from exc
        except httpx.TransportError as exc:
            raise LLMUnavailableError(
                f"openrouter transport: {exc}"
            ) from exc
        elapsed = time.monotonic() - t0

        return self._parse_response(response, req=req, elapsed=elapsed)

    def _build_body(self, req: LLMRequest) -> dict[str, Any]:
        # Inject the JSON schema into the system prompt as a textual contract,
        # so models that don't honour ``response_format`` still produce JSON.
        schema_text = json.dumps(req.json_schema, ensure_ascii=False, indent=2)
        augmented_system = (
            f"{req.system}\n\n"
            f"Return ONLY a single valid JSON object that conforms to this "
            f"JSON Schema. Do not wrap it in Markdown fences:\n{schema_text}"
        )
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": augmented_system},
                {"role": "user", "content": req.user_text},
            ],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": req.schema_name,
                    "schema": req.json_schema,
                    "strict": True,
                },
            },
        }

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        req: LLMRequest,
        elapsed: float,
    ) -> LLMResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMUnavailableError(
                f"openrouter returned non-JSON envelope: {exc}"
            ) from exc

        try:
            content = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailableError(
                f"openrouter response missing choices/message/content: {exc}"
            ) from exc

        usage = payload.get("usage") or {}
        in_tokens = int(usage.get("prompt_tokens") or 0)
        out_tokens = int(usage.get("completion_tokens") or 0)

        try:
            data = json.loads(content)
            parsed_via = "json"
        except (json.JSONDecodeError, TypeError):
            try:
                repaired = repair_json(content)
                data = json.loads(repaired)
                parsed_via = "json_repair"
            except (json.JSONDecodeError, ValueError) as exc:
                raise LLMUnavailableError(
                    f"openrouter content is not valid JSON even after repair: "
                    f"{exc}"
                ) from exc

        if not isinstance(data, dict):
            raise LLMUnavailableError(
                "openrouter content parsed but is not a JSON object"
            )

        self._log.info(
            "llm_request_completed",
            provider=self.name,
            model=self.model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            elapsed_s=round(elapsed, 4),
            status="success",
            parsed_via=parsed_via,
        )
        return LLMResponse(
            data=data,
            raw_text=content,
            provider=self.name,
            model=self.model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )
