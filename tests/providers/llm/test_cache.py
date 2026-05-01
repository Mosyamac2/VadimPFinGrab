"""CachedLLMProvider + cache key determinism tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from edx.providers.llm.base import LLMRequest, LLMResponse
from edx.providers.llm.cache import CachedLLMProvider, request_cache_key


@dataclass
class _CountingProvider:
    name: str = "stub"
    supports_pdf_input: bool = False
    response_data: dict[str, Any] = field(default_factory=lambda: {"metric": "x", "value": 1.0})
    calls: int = 0

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            data=self.response_data,
            raw_text="ok",
            provider=self.name,
            model="m",
            input_tokens=1,
            output_tokens=1,
        )


def test_request_cache_key_deterministic_and_sensitive(
    request_factory: Callable[..., LLMRequest],
) -> None:
    a = request_factory(system="S", user_text="U")
    b = request_factory(system="S", user_text="U")
    assert request_cache_key(a) == request_cache_key(b)

    c = request_factory(system="S", user_text="DIFFERENT")
    assert request_cache_key(a) != request_cache_key(c)

    d = request_factory(pdf_bytes=b"abc")
    e = request_factory(pdf_bytes=b"def")
    assert request_cache_key(d) != request_cache_key(e)


@pytest.mark.asyncio
async def test_second_call_is_cache_hit(
    tmp_path: Path, request_factory: Callable[..., LLMRequest]
) -> None:
    inner = _CountingProvider()
    cache_dir = tmp_path / "_llm_cache"
    cached = CachedLLMProvider(inner, cache_dir)

    req = request_factory(user_text="hello")
    first = await cached.complete(req)
    second = await cached.complete(req)

    assert inner.calls == 1
    assert first.data == second.data
    assert first.provider == second.provider == "stub"
    assert (cache_dir / f"{request_cache_key(req)}.json").is_file()


@pytest.mark.asyncio
async def test_different_request_misses_cache(
    tmp_path: Path, request_factory: Callable[..., LLMRequest]
) -> None:
    inner = _CountingProvider()
    cached = CachedLLMProvider(inner, tmp_path / "_llm_cache")

    await cached.complete(request_factory(user_text="A"))
    await cached.complete(request_factory(user_text="B"))
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_corrupt_cache_entry_is_overwritten(
    tmp_path: Path, request_factory: Callable[..., LLMRequest]
) -> None:
    inner = _CountingProvider()
    cache_dir = tmp_path / "_llm_cache"
    cache_dir.mkdir()
    req = request_factory()
    bad = cache_dir / f"{request_cache_key(req)}.json"
    bad.write_text("{not json", encoding="utf-8")

    cached = CachedLLMProvider(inner, cache_dir)
    await cached.complete(req)
    # The corrupt file is replaced with a valid one.
    assert inner.calls == 1
    assert bad.read_text(encoding="utf-8").startswith("{")


def test_supports_pdf_input_delegates_to_inner() -> None:
    inner = _CountingProvider(supports_pdf_input=True)
    cached = CachedLLMProvider(inner, Path("/tmp/none"))
    assert cached.supports_pdf_input is True
    assert cached.name == "cached:stub"
