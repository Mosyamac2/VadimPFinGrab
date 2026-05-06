"""Patch 23: ``build_http_client`` dispatches by ``app.discoverer.http_backend``."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from edx.config import load_all
from edx.http import build_http_client

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def test_build_http_client_returns_httpx_by_default(tmp_path: Path) -> None:
    settings = load_all(REPO_CONFIG_DIR, env_file=tmp_path / "missing.env")
    # Config uses playwright backend (operator-configured for VPS deployment).
    assert settings.app.discoverer.http_backend == "playwright"
    from edx.http.playwright_client import PlaywrightEDisclosureClient

    client = build_http_client(settings)
    assert isinstance(client, PlaywrightEDisclosureClient)


def test_build_http_client_returns_playwright_when_configured(
    tmp_path: Path,
) -> None:
    """Constructor returns the right subclass without touching Playwright
    itself — the heavy ``async_playwright().start()`` only happens in
    ``__aenter__``, so this test runs even on a host without the
    optional dependency."""
    settings = load_all(REPO_CONFIG_DIR, env_file=tmp_path / "missing.env")
    settings.app.discoverer.http_backend = "playwright"
    from edx.http.playwright_client import PlaywrightEDisclosureClient

    client = build_http_client(settings)
    assert isinstance(client, PlaywrightEDisclosureClient)


def test_playwright_client_get_outside_context_raises() -> None:
    """The browser is started in __aenter__; calling get() before it
    is set up should fail loudly, not return a phantom response."""
    from edx.http.playwright_client import PlaywrightEDisclosureClient

    client = PlaywrightEDisclosureClient(
        user_agent="edx-test/1.0", respect_robots=False
    )
    import asyncio

    with pytest.raises(RuntimeError, match="async with"):
        asyncio.run(client.get("/portal/files.aspx"))


def test_playwright_aenter_without_package_installed_raises_runtime_error(
    tmp_path: Path,
) -> None:
    """Without ``playwright`` installed, ``async with`` surfaces a clear
    install hint instead of a cryptic ImportError. Skip when the package
    IS installed — there's nothing to assert in that case here."""
    if importlib.util.find_spec("playwright") is not None:
        pytest.skip("playwright is installed; this test covers the missing case")
    settings = load_all(REPO_CONFIG_DIR, env_file=tmp_path / "missing.env")
    settings.app.discoverer.http_backend = "playwright"
    client = build_http_client(settings)
    import asyncio

    async def _enter() -> None:
        async with client:
            pass

    with pytest.raises(RuntimeError, match="pip install playwright"):
        asyncio.run(_enter())


def test_playwright_module_imports_without_playwright_installed() -> None:
    """Importing the Playwright client module must NOT require Playwright
    — only ``__aenter__`` does. This keeps the default httpx path immune
    to the optional dependency being missing."""
    sys.modules.pop("edx.http.playwright_client", None)
    importlib.import_module("edx.http.playwright_client")  # must not raise
