"""Unit tests for ``tools/validate_tickers.py`` — pure logic, no network."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from edx.config import TickerEntry

# Load the script as a module without requiring an installed entrypoint.
_TOOL_PATH = (
    Path(__file__).resolve().parents[2] / "tools" / "validate_tickers.py"
)
_spec = importlib.util.spec_from_file_location("validate_tickers", _TOOL_PATH)
assert _spec is not None and _spec.loader is not None
validate_tickers_mod = importlib.util.module_from_spec(_spec)
sys.modules["validate_tickers"] = validate_tickers_mod
_spec.loader.exec_module(validate_tickers_mod)


# --- pure classifier --------------------------------------------------------


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (404, "<html>nope</html>", "MISSING"),
        (410, "", "MISSING"),
        (500, "boom", "ERROR"),
        (200, "", "MISSING"),
        (200, "<html><body><div>nothing here</div></body></html>", "MISSING"),
        (
            200,
            "<html><body><table class='files-table'><tbody>"
            "<tr><th>h</th></tr>"
            "</tbody></table></body></html>",
            "EMPTY",
        ),
        (
            200,
            "<html><body><table class='files-table'><tbody>"
            "<tr><th>h</th></tr>"
            "<tr><td>row</td><td>data</td></tr>"
            "</tbody></table></body></html>",
            "OK",
        ),
    ],
)
def test_classify_response_matrix(status: int, body: str, expected: str) -> None:
    assert validate_tickers_mod.classify_response(status, body) == expected


# --- end-to-end logic with a mock client ----------------------------------


@dataclass
class _FakeResponse:
    status_code: int
    text: str


@dataclass
class _FakeClient:
    """``responses[(ticker_id, type_code)] -> (status, body)``."""

    responses: dict[tuple[str, int], tuple[int, str]]
    calls: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.calls = []

    async def get(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        # Parse "/portal/files.aspx?id=X&type=Y"
        params = url.split("?", 1)[1]
        kv = dict(part.split("=", 1) for part in params.split("&"))
        key = (kv["id"], int(kv["type"]))
        status, body = self.responses.get(key, (404, ""))
        return _FakeResponse(status_code=status, text=body)


def _table_html(rows: int = 1) -> str:
    """Tiny ``table.files-table`` snippet with N data rows."""
    body = "".join(
        "<tr><td>r</td><td>data</td></tr>" for _ in range(rows)
    )
    return (
        "<html><body><table class='files-table'><tbody>"
        f"<tr><th>h</th></tr>{body}</tbody></table></body></html>"
    )


@pytest.mark.asyncio
async def test_lkoh_like_passes_with_one_type_ok() -> None:
    """LKOH-style: id=17, type=4 missing, type=3 OK → pass=True."""
    client = _FakeClient(
        responses={
            ("17", 2): (200, _table_html()),
            ("17", 3): (200, _table_html(rows=5)),
            ("17", 4): (404, ""),
            ("17", 5): (404, ""),
        }
    )
    tickers = [
        TickerEntry(
            ticker="LKOH",
            e_disclosure_id="17",
            name="Lukoil",
            profile="non_bank",
        )
    ]
    results = await validate_tickers_mod.validate_tickers(client, tickers)
    assert len(results) == 1
    r = results[0]
    assert r.passes is True
    assert r.statuses == {2: "OK", 3: "OK", 4: "MISSING", 5: "MISSING"}
    # Probed every type, in order.
    assert client.calls == [
        "/portal/files.aspx?id=17&type=2",
        "/portal/files.aspx?id=17&type=3",
        "/portal/files.aspx?id=17&type=4",
        "/portal/files.aspx?id=17&type=5",
    ]


@pytest.mark.asyncio
async def test_no_metric_source_fails() -> None:
    """type=2 alone (annual report) doesn't carry the pipeline → pass=False."""
    client = _FakeClient(
        responses={
            ("99", 2): (200, _table_html()),
            ("99", 3): (200, "<html><body></body></html>"),
            ("99", 4): (404, ""),
            ("99", 5): (404, ""),
        }
    )
    tickers = [
        TickerEntry(
            ticker="X", e_disclosure_id="99", name="X", profile="non_bank"
        )
    ]
    results = await validate_tickers_mod.validate_tickers(client, tickers)
    assert results[0].passes is False
    assert results[0].statuses == {
        2: "OK",
        3: "MISSING",
        4: "MISSING",
        5: "MISSING",
    }


@pytest.mark.asyncio
async def test_5xx_classified_as_error() -> None:
    client = _FakeClient(
        responses={
            ("1", 2): (500, "boom"),
            ("1", 3): (500, "boom"),
            ("1", 4): (200, _table_html()),
            ("1", 5): (404, ""),
        }
    )
    tickers = [
        TickerEntry(
            ticker="A", e_disclosure_id="1", name="A", profile="non_bank"
        )
    ]
    results = await validate_tickers_mod.validate_tickers(client, tickers)
    r = results[0]
    assert r.statuses == {2: "ERROR", 3: "ERROR", 4: "OK", 5: "MISSING"}
    # Still passes — type=4 is OK and counts as a metric source.
    assert r.passes is True


@pytest.mark.asyncio
async def test_summarise_counts_passing_correctly() -> None:
    client = _FakeClient(
        responses={
            ("1", 2): (404, ""),
            ("1", 3): (200, _table_html()),
            ("1", 4): (404, ""),
            ("1", 5): (404, ""),
            ("2", 2): (404, ""),
            ("2", 3): (404, ""),
            ("2", 4): (404, ""),
            ("2", 5): (404, ""),
        }
    )
    tickers = [
        TickerEntry(
            ticker="A", e_disclosure_id="1", name="A", profile="non_bank"
        ),
        TickerEntry(
            ticker="B", e_disclosure_id="2", name="B", profile="non_bank"
        ),
    ]
    results = await validate_tickers_mod.validate_tickers(client, tickers)
    passing, total = validate_tickers_mod.summarise(results)
    assert passing == 1 and total == 2


def test_format_table_renders_without_errors() -> None:
    """Render path is covered so a regression in format_table is caught."""
    from validate_tickers import TickerProbeResult  # type: ignore[import-not-found]

    sample = TickerProbeResult(
        ticker="X",
        e_disclosure_id="42",
        statuses={2: "OK", 3: "EMPTY", 4: "MISSING", 5: "ERROR"},
        error_messages={5: "ConnectionResetError: boom"},
    )
    text = validate_tickers_mod.format_table([sample])
    assert "X" in text
    assert "type=4" in text
    # Errors block surfaced.
    assert "ConnectionResetError" in text
