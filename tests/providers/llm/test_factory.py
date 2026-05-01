"""Factory: chain construction depends on which keys are set."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from edx.config import load_all
from edx.providers.llm.anthropic_provider import AnthropicLLMProvider
from edx.providers.llm.base import LLMUnavailableError
from edx.providers.llm.cache import CachedLLMProvider
from edx.providers.llm.chain import FallbackChain
from edx.providers.llm.factory import build_llm_provider
from edx.providers.llm.openrouter_provider import OpenRouterLLMProvider

REPO_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def _settings(tmp_path: Path):  # type: ignore[no-untyped-def]
    cfg = tmp_path / "config"
    cfg.mkdir()
    for src in REPO_CONFIG_DIR.glob("*.yaml"):
        (cfg / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return load_all(cfg)


def _patch_secrets(settings, *, anthropic: str | None, openrouter: str | None):  # type: ignore[no-untyped-def]
    """Replace the SecretStr-typed fields on a loaded AppSettings."""
    from pydantic import SecretStr

    settings.secrets.anthropic_api_key = SecretStr(anthropic) if anthropic else None
    settings.secrets.openrouter_api_key = SecretStr(openrouter) if openrouter else None
    return settings


def test_no_keys_raises_with_helpful_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ensure no env-sourced secrets leak in.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = _settings(tmp_path)
    settings.secrets.anthropic_api_key = None
    settings.secrets.openrouter_api_key = None
    with pytest.raises(LLMUnavailableError, match="No LLM providers"):
        build_llm_provider(settings)


def test_only_anthropic_returns_single_provider_cached(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _patch_secrets(settings, anthropic="sk-ant-1", openrouter=None)
    provider = build_llm_provider(settings)
    assert isinstance(provider, CachedLLMProvider)
    assert isinstance(provider.inner, AnthropicLLMProvider)


def test_only_openrouter_returns_single_provider_cached(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _patch_secrets(settings, anthropic=None, openrouter="or-1")
    provider = build_llm_provider(settings)
    assert isinstance(provider, CachedLLMProvider)
    assert isinstance(provider.inner, OpenRouterLLMProvider)


def test_both_keys_returns_chain_with_anthropic_first(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _patch_secrets(settings, anthropic="sk-ant", openrouter="or-1")
    provider = build_llm_provider(settings)
    assert isinstance(provider, CachedLLMProvider)
    chain = provider.inner
    assert isinstance(chain, FallbackChain)
    assert chain.providers[0].name == "anthropic"
    assert chain.providers[1].name == "openrouter"


def test_cache_disabled_returns_raw_chain(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _patch_secrets(settings, anthropic="sk-ant", openrouter="or-1")
    # Disable cache via the loaded config.
    llm_path = tmp_path / "config" / "llm.yaml"
    data = yaml.safe_load(llm_path.read_text(encoding="utf-8"))
    data["cache_enabled"] = False
    llm_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    settings = load_all(tmp_path / "config")
    _patch_secrets(settings, anthropic="sk-ant", openrouter="or-1")
    provider = build_llm_provider(settings)
    assert isinstance(provider, FallbackChain)


def test_chain_built_when_only_one_key_set(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _patch_secrets(settings, anthropic=None, openrouter="or-1")
    provider = build_llm_provider(settings)
    # Single provider — not wrapped in FallbackChain (cache may wrap).
    inner = (
        provider.inner if isinstance(provider, CachedLLMProvider) else provider
    )
    assert not isinstance(inner, FallbackChain)
