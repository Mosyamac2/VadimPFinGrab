"""Pure-function coverage for ``tools/find_e_disclosure_ids.py``."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "find_e_disclosure_ids.py"
)
_spec = importlib.util.spec_from_file_location(
    "find_e_disclosure_ids", _TOOL_PATH
)
assert _spec is not None and _spec.loader is not None
find_mod = importlib.util.module_from_spec(_spec)
sys.modules["find_e_disclosure_ids"] = find_mod
_spec.loader.exec_module(find_mod)


def test_parse_search_results_extracts_company_ids() -> None:
    html = """
    <html><body>
      <ul class="results">
        <li><a href="/portal/company.aspx?id=3043">ПАО Сбербанк</a></li>
        <li><a href="/portal/company.aspx?id=17&amp;extra=1">ПАО ЛУКОЙЛ</a></li>
        <li><a href="/portal/news/x.html">Not a company link</a></li>
      </ul>
    </body></html>
    """
    out = find_mod.parse_search_results(html)
    ids = {x[0] for x in out}
    assert ids == {"3043", "17"}
    by_id = {iid: name for iid, name, _ in out}
    assert by_id["3043"].startswith("ПАО Сбербанк")
    assert by_id["17"].startswith("ПАО ЛУКОЙЛ")


def test_parse_search_results_returns_empty_when_no_links() -> None:
    assert find_mod.parse_search_results("<html><body></body></html>") == []


def test_rank_candidates_orders_by_similarity() -> None:
    candidates = [
        ("999", "Газпром нефть", "/portal/company.aspx?id=999"),
        ("3043", "ПАО Сбербанк", "/portal/company.aspx?id=3043"),
        ("100", "Сбер Девелопмент", "/portal/company.aspx?id=100"),
    ]
    ranked = find_mod.rank_candidates(
        candidates, target_name="ПАО Сбербанк", top_n=3
    )
    assert ranked[0][0] == "3043"
    # Confidence is in [0, 1] and the leader is exact-match-ish.
    assert ranked[0][2] >= 0.95


def test_rank_candidates_top_n_truncation() -> None:
    candidates = [(str(i), f"Name {i}", "/x") for i in range(10)]
    ranked = find_mod.rank_candidates(
        candidates, target_name="Name 0", top_n=3
    )
    assert len(ranked) == 3


def test_main_async_uses_build_http_client_not_direct_client() -> None:
    """_main_async must delegate HTTP client creation to build_http_client
    so the configured http_backend (e.g. playwright) is respected, not
    hard-coded to the plain httpx EDisclosureClient."""
    from unittest.mock import MagicMock as _MM

    # Fake settings: one ticker named FAKE that matches the --tickers filter.
    # We mock load_all so the test is independent of the config-evolve directory
    # (which only contains the current batch's tickers and changes every tick).
    fake_ticker = _MM()
    fake_ticker.ticker = "FAKE"
    fake_ticker.name = "Fake Company"

    fake_settings = _MM()
    fake_settings.tickers.tickers = [fake_ticker]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body></body></html>"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    build_calls: list[object] = []

    def fake_build_http_client(settings: object) -> object:
        build_calls.append(settings)
        return mock_client

    with (
        patch("find_e_disclosure_ids.load_all", return_value=fake_settings),
        patch("edx.http.build_http_client", fake_build_http_client),
    ):
        result = asyncio.run(
            find_mod._main_async(
                ["--tickers", "FAKE", "--config-dir", "/nonexistent"]
            )
        )

    assert result == 0
    assert len(build_calls) == 1, "build_http_client must be called exactly once"
