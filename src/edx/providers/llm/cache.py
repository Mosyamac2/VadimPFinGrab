"""Persistent cache wrapper for LLM providers.

Cache key = SHA-256 of (system + user_text + pdf_bytes_hash + sorted JSON
schema). Hits are read straight from
``data/processed/_llm_cache/{key}.json``; misses fall through to the wrapped
provider, then the response is written back. Idempotency for free.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from edx.logging_setup import get_logger
from edx.providers.llm.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
)


def request_cache_key(req: LLMRequest) -> str:
    """Deterministic cache key for an :class:`LLMRequest`."""
    h = hashlib.sha256()
    h.update(req.system.encode("utf-8"))
    h.update(b"\x00")
    h.update(req.user_text.encode("utf-8"))
    h.update(b"\x00")
    if req.pdf_bytes:
        h.update(hashlib.sha256(req.pdf_bytes).digest())
    h.update(b"\x00")
    schema_blob = json.dumps(
        req.json_schema, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    h.update(schema_blob)
    h.update(b"\x00")
    h.update(req.schema_name.encode("utf-8"))
    return h.hexdigest()


class CachedLLMProvider:
    """Wraps any :class:`LLMProvider` with an on-disk JSON cache."""

    def __init__(self, inner: LLMProvider, cache_dir: Path) -> None:
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.name = f"cached:{inner.name}"
        self.supports_pdf_input = inner.supports_pdf_input
        self._log = get_logger("edx.providers.llm.cache")

    async def complete(self, req: LLMRequest) -> LLMResponse:
        key = request_cache_key(req)
        path = self.cache_dir / f"{key}.json"

        cached = self._load(path)
        if cached is not None:
            self._log.info(
                "llm_cache_hit",
                provider=self.inner.name,
                key=key,
            )
            return cached

        response = await self.inner.complete(req)
        self._save(path, response)
        return response

    def _load(self, path: Path) -> LLMResponse | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return LLMResponse.model_validate(payload)
        except (json.JSONDecodeError, ValueError):
            self._log.warning("llm_cache_corrupt", path=str(path))
            return None

    def _save(self, path: Path, response: LLMResponse) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            response.model_dump_json(indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)
