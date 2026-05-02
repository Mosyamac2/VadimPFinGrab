#!/usr/bin/env python3
"""Probe each ticker in ``config/tickers.yaml`` against e-disclosure.ru.

After editing ``config/tickers.yaml`` (changing ``e_disclosure_id``,
adding/removing entries), run::

    python tools/validate_tickers.py [--strict]

For every (ticker, type) ∈ ``{2, 3, 4, 5}`` × tickers the script GETs
``/portal/files.aspx?id=X&type=Y`` through the same :class:`EDisclosureClient`
the pipeline uses and reports one of:

- ``OK``      — 200 + ``table.files-table`` is present and ``tbody`` has rows.
- ``EMPTY``   — 200 + ``table.files-table`` is present but ``tbody`` is empty
                (the issuer publishes this type, just nothing in scope yet).
- ``MISSING`` — 200 with no table, or 404/410 (the issuer doesn't publish
                this type via the configured ``e_disclosure_id``; canonical
                example: LKOH ``id=17`` has no ``type=4``).
- ``ERROR``   — 5xx, timeout, parsing exception.

A ticker "passes" when at least one of ``type∈{3,4,5}`` returns ``OK``
(without any source of metric data, the rest of the pipeline can't move).
``MISSING`` for a single type is **information**, not a failure.

With ``--strict`` the script exits non-zero only when at least one ticker
has zero passing types — catches the ``REPLACE_ME`` typo + bad-id cases
without rejecting issuers that simply don't publish all four types.

The validation logic itself lives in :func:`validate_tickers` so the tests
can exercise it with a mocked client; the ``main()`` only assembles the
real client + walks the config.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

# Make ``edx`` importable when the script is run from a checkout.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from selectolax.parser import HTMLParser  # noqa: E402

from edx.config import TickerEntry, load_all  # noqa: E402

TypeStatus = Literal["OK", "EMPTY", "MISSING", "ERROR"]
TYPE_CODES: tuple[int, ...] = (2, 3, 4, 5)
# A ticker passes when at least one of these types is OK — type=2 alone
# (annual report, not a financial statement) can't carry the pipeline.
METRIC_SOURCE_TYPES: tuple[int, ...] = (3, 4, 5)


@dataclass(frozen=True)
class TickerProbeResult:
    ticker: str
    e_disclosure_id: str
    statuses: dict[int, TypeStatus]   # type_code → status
    error_messages: dict[int, str]    # type_code → str (only for ERROR)

    @property
    def passes(self) -> bool:
        return any(self.statuses.get(t) == "OK" for t in METRIC_SOURCE_TYPES)


class _AsyncHttpClient(Protocol):
    """Minimal subset used by :func:`validate_tickers` for testability."""

    async def get(self, url: str) -> object: ...


def classify_response(
    status_code: int, body_html: str
) -> TypeStatus:
    """Pure classifier from a (status, body) pair to a probe status.

    Mirrors the parser's view-source unwrap — ``HTMLParser(...).body.text()``
    decodes Firefox view-source snapshots — so the logic stays consistent
    with what the Discoverer would parse.
    """
    if status_code in (404, 410):
        return "MISSING"
    if status_code != 200:
        return "ERROR"
    if not body_html.strip():
        return "MISSING"
    real_html = _maybe_unwrap_view_source(body_html)
    tree = HTMLParser(real_html)
    table = tree.css_first("table.files-table")
    if table is None:
        return "MISSING"
    tbody = table.css_first("tbody") or table
    has_data_row = any(
        row.css_first("th") is None and row.css("td")
        for row in tbody.css("tr")
    )
    return "OK" if has_data_row else "EMPTY"


def _maybe_unwrap_view_source(html: str) -> str:
    if "<table" in html:
        return html
    tree = HTMLParser(html)
    if tree.body is None:
        return html
    decoded = tree.body.text()
    return decoded if "<table" in decoded else html


async def _probe_one(
    client: _AsyncHttpClient, ticker: TickerEntry, type_code: int
) -> tuple[TypeStatus, str]:
    """Single (ticker, type) probe. Returns (status, error_message)."""
    url = f"/portal/files.aspx?id={ticker.e_disclosure_id}&type={type_code}"
    try:
        response = await client.get(url)  # type: ignore[func-returns-value]
        status_code = int(response.status_code)  # type: ignore[attr-defined]
        text = str(response.text)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — fail-soft per ticker
        return "ERROR", f"{type(exc).__name__}: {exc}"
    return classify_response(status_code, text), ""


async def validate_tickers(
    client: _AsyncHttpClient, tickers: list[TickerEntry]
) -> list[TickerProbeResult]:
    """Walk every (ticker, type∈{2,3,4,5}) combination through ``client``.

    Pure-function (sans I/O via the injected client) so the tests can
    drive it with a mock.
    """
    results: list[TickerProbeResult] = []
    for ticker in tickers:
        statuses: dict[int, TypeStatus] = {}
        errors: dict[int, str] = {}
        for type_code in TYPE_CODES:
            status, error = await _probe_one(client, ticker, type_code)
            statuses[type_code] = status
            if error:
                errors[type_code] = error
        results.append(
            TickerProbeResult(
                ticker=ticker.ticker,
                e_disclosure_id=ticker.e_disclosure_id,
                statuses=statuses,
                error_messages=errors,
            )
        )
    return results


# --- CLI plumbing ----------------------------------------------------------


def format_table(results: list[TickerProbeResult]) -> str:
    """Pretty-printable matrix of probe outcomes."""
    header = (
        f"{'ticker':<8} {'id':<10} "
        + "  ".join(f"type={t}" for t in TYPE_CODES)
        + "  passes"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        cells = "  ".join(
            f"{r.statuses.get(t, '???'):<7}" for t in TYPE_CODES
        )
        passes = "yes" if r.passes else "NO"
        lines.append(
            f"{r.ticker:<8} {r.e_disclosure_id:<10} {cells}  {passes}"
        )
    # Errors verbatim under the table — operators want to see them once.
    error_lines = []
    for r in results:
        for type_code, msg in r.error_messages.items():
            error_lines.append(f"  ! {r.ticker} type={type_code}: {msg}")
    if error_lines:
        lines.append("")
        lines.append("Errors:")
        lines.extend(error_lines)
    return "\n".join(lines)


def summarise(results: list[TickerProbeResult]) -> tuple[int, int]:
    passing = sum(1 for r in results if r.passes)
    return passing, len(results)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe every ticker in config/tickers.yaml against e-disclosure"
        )
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=ROOT / "config",
        help="Path to the directory holding tickers.yaml (default: ./config)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any ticker has zero passing types",
    )
    return parser.parse_args(argv)


async def _main_async(argv: list[str]) -> int:
    args = _parse_args(argv)
    settings = load_all(args.config_dir)

    # Refuse to probe an obviously unfilled tickers.yaml.
    bad_ids = [
        t for t in settings.tickers.tickers if t.e_disclosure_id == "REPLACE_ME"
    ]
    if bad_ids:
        print(
            "Some tickers still carry e_disclosure_id=REPLACE_ME — "
            "fill them in (try `python tools/find_e_disclosure_ids.py`) before validating:",
            file=sys.stderr,
        )
        for t in bad_ids:
            print(f"  - {t.ticker}: {t.name}", file=sys.stderr)
        return 2

    # Lazy import so the unit tests don't pull httpx at collection time.
    from edx.http.client import EDisclosureClient, build_user_agent

    cfg = settings.app.discoverer
    async with EDisclosureClient(
        base_url=cfg.base_url,
        user_agent=build_user_agent(settings),
        requests_per_second=cfg.requests_per_second,
        request_timeout_s=cfg.request_timeout_s,
        max_retries=cfg.max_retries,
        retry_min_wait_s=cfg.retry_min_wait_s,
        retry_max_wait_s=cfg.retry_max_wait_s,
        respect_robots=cfg.respect_robots,
        cookies=cfg.cookies or None,
    ) as client:
        results = await validate_tickers(client, settings.tickers.tickers)

    print(format_table(results))
    passing, total = summarise(results)
    print(f"\n{passing}/{total} tickers pass.")
    if args.strict and passing < total:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main_async(list(argv) if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
