"""Factory: Anthropic-only provider construction.

Per operator decision (May 2026) the OpenRouter fallback was removed —
all LLM calls go through ``ANTHROPIC_API_KEY`` and a missing/dead key
fast-fails rather than silently switching vendors.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from edx.config import load_all
from edx.providers.llm.anthropic_provider import AnthropicLLMProvider
from edx.providers.llm.base import LLMUnavailableError
from edx.providers.llm.cache import CachedLLMProvider
from edx.providers.llm.factory import build_llm_provider

REPO_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def _settings(tmp_path: Path):  # type: ignore[no-untyped-def]
    cfg = tmp_path / "config"
    cfg.mkdir()
    for src in REPO_CONFIG_DIR.glob("*.yaml"):
        (cfg / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return load_all(cfg)


def _patch_secrets(settings, *, anthropic: str | None):  # type: ignore[no-untyped-def]
    """Replace the SecretStr-typed anthropic field on a loaded AppSettings."""
    from pydantic import SecretStr

    settings.secrets.anthropic_api_key = SecretStr(anthropic) if anthropic else None
    # OpenRouter key may still be present in env/secrets but is no longer wired.
    settings.secrets.openrouter_api_key = None
    return settings


def test_no_anthropic_key_raises_with_helpful_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = _settings(tmp_path)
    settings.secrets.anthropic_api_key = None
    settings.secrets.openrouter_api_key = None
    with pytest.raises(LLMUnavailableError, match="ANTHROPIC_API_KEY"):
        build_llm_provider(settings)


def test_anthropic_key_returns_single_provider_cached(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _patch_secrets(settings, anthropic="sk-ant-1")
    provider = build_llm_provider(settings)
    assert isinstance(provider, CachedLLMProvider)
    assert isinstance(provider.inner, AnthropicLLMProvider)


def test_openrouter_key_alone_no_longer_provisions_a_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anti-regression: OpenRouter is no longer a fallback. If only the
    OpenRouter key is set and ``ANTHROPIC_API_KEY`` is absent, the
    factory must raise — not silently route to OpenRouter."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = _settings(tmp_path)
    settings.secrets.anthropic_api_key = None
    # Pretend OpenRouter is set; should be ignored.
    from pydantic import SecretStr

    settings.secrets.openrouter_api_key = SecretStr("or-1")
    with pytest.raises(LLMUnavailableError, match="ANTHROPIC_API_KEY"):
        build_llm_provider(settings)


def test_cache_disabled_returns_raw_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _patch_secrets(settings, anthropic="sk-ant")
    # Disable cache via the loaded config.
    llm_path = tmp_path / "config" / "llm.yaml"
    data = yaml.safe_load(llm_path.read_text(encoding="utf-8"))
    data["cache_enabled"] = False
    llm_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    settings = load_all(tmp_path / "config")
    _patch_secrets(settings, anthropic="sk-ant")
    provider = build_llm_provider(settings)
    assert isinstance(provider, AnthropicLLMProvider)
