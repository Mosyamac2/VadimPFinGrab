"""Pure-function coverage for ``tools/find_e_disclosure_ids.py``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
