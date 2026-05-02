#!/usr/bin/env python3
"""Suggest ``e_disclosure_id`` values for the tickers in your config.

Run this after adding a new ticker to ``config/tickers.yaml`` (with a
placeholder ``e_disclosure_id``) to find the right numeric id without
clicking through the e-disclosure search UI by hand::

    python tools/find_e_disclosure_ids.py
    python tools/find_e_disclosure_ids.py --tickers SBER,LKOH
    python tools/find_e_disclosure_ids.py --out /tmp/ids.csv

For each ticker:

1. GETs the e-disclosure search endpoint
   ``/poisk-po-kompaniyam/?queryString=<name>`` with the issuer name
   from ``tickers.yaml``.
2. Parses every ``href="…company.aspx?id=N"`` link out of the response.
3. Ranks candidates by ``difflib.SequenceMatcher`` similarity to the
   configured name; prints the top 3 with their similarity score.

The script **does not modify** ``tickers.yaml`` — id resolution requires
human judgement (group-vs-subsidiary, namesakes, defunct entities). The
script is a recall booster, not an authoritative source.

Rate-limited to 1 RPS (same polite-scraping rules as the pipeline).

Note on anti-bot:
The e-disclosure search page is fronted by ServicePipe; if requests come
back as a JavaScript challenge instead of the search HTML, set
``app.discoverer.cookies`` in ``config/app.yaml`` (paste a logged-in
browser cookie) — same workaround as for the Discoverer stage.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from selectolax.parser import HTMLParser  # noqa: E402

from edx.config import TickerEntry, load_all  # noqa: E402

SEARCH_PATH = "/poisk-po-kompaniyam/"


@dataclass(frozen=True)
class Suggestion:
    ticker: str
    configured_name: str
    e_disclosure_id: str
    name_in_search: str
    confidence: float          # 0.0 .. 1.0
    url: str


class _AsyncHttpClient(Protocol):
    async def get(self, url: str) -> object: ...


def parse_search_results(html: str) -> list[tuple[str, str, str]]:
    """Return ``(id, anchor_text, absolute_url)`` for every issuer link.

    Targets ``<a href="…company.aspx?id=N">`` anchors. Two link classes
    are observed on the live search page: ``.companyLink`` and plain
    ``a[href*="company.aspx"]`` — we accept both.
    """
    real_html = _maybe_unwrap_view_source(html)
    tree = HTMLParser(real_html)
    out: list[tuple[str, str, str]] = []
    for anchor in tree.css('a[href*="company.aspx"]'):
        href = (anchor.attributes.get("href") or "").strip()
        if not href:
            continue
        match = re.search(r"company\.aspx\?id=(\d+)", href)
        if match is None:
            continue
        text = (anchor.text(strip=True) or "").strip()
        out.append((match.group(1), text, href))
    return out


def rank_candidates(
    candidates: list[tuple[str, str, str]],
    target_name: str,
    *,
    top_n: int = 3,
) -> list[tuple[str, str, float, str]]:
    """Sort candidates by name similarity to ``target_name``.

    Returns the top ``top_n`` with their similarity score.
    """
    target = target_name.lower().strip()
    scored = []
    for issuer_id, name_in_search, url in candidates:
        score = difflib.SequenceMatcher(
            None, target, name_in_search.lower().strip()
        ).ratio()
        scored.append((issuer_id, name_in_search, score, url))
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:top_n]


def _maybe_unwrap_view_source(html: str) -> str:
    if "<a" in html and "company.aspx" in html:
        return html
    tree = HTMLParser(html)
    if tree.body is None:
        return html
    decoded = tree.body.text()
    if "company.aspx" in decoded:
        return decoded
    return html


async def find_ids(
    client: _AsyncHttpClient, tickers: list[TickerEntry]
) -> list[Suggestion]:
    suggestions: list[Suggestion] = []
    for ticker in tickers:
        url = f"{SEARCH_PATH}?queryString={quote_plus(ticker.name)}"
        try:
            response = await client.get(url)  # type: ignore[func-returns-value]
            status = int(response.status_code)  # type: ignore[attr-defined]
            text = str(response.text)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            print(
                f"! {ticker.ticker}: search failed — {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue
        if status != 200:
            print(
                f"! {ticker.ticker}: search HTTP {status}", file=sys.stderr
            )
            continue
        candidates = parse_search_results(text)
        if not candidates:
            print(
                f"  {ticker.ticker}: no candidates on search page",
                file=sys.stderr,
            )
            continue
        for issuer_id, name_in_search, score, candidate_url in rank_candidates(
            candidates, ticker.name
        ):
            suggestions.append(
                Suggestion(
                    ticker=ticker.ticker,
                    configured_name=ticker.name,
                    e_disclosure_id=issuer_id,
                    name_in_search=name_in_search,
                    confidence=round(score, 3),
                    url=candidate_url,
                )
            )
    return suggestions


# --- CLI plumbing ----------------------------------------------------------


def format_table(suggestions: list[Suggestion]) -> str:
    header = (
        f"{'ticker':<8} {'sug_id':<10} {'conf':<6} "
        f"{'name_in_search':<60} url"
    )
    lines = [header, "-" * len(header)]
    last_ticker = None
    for s in suggestions:
        # Visual gap between tickers makes scanning easier.
        if last_ticker is not None and s.ticker != last_ticker:
            lines.append("")
        last_ticker = s.ticker
        lines.append(
            f"{s.ticker:<8} {s.e_disclosure_id:<10} "
            f"{s.confidence:<6.3f} "
            f"{s.name_in_search[:60]:<60} {s.url}"
        )
    return "\n".join(lines)


def write_csv(path: Path, suggestions: list[Suggestion]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["ticker", "configured_name", "suggested_id",
             "name_in_search", "confidence", "url"]
        )
        for s in suggestions:
            writer.writerow(
                [
                    s.ticker,
                    s.configured_name,
                    s.e_disclosure_id,
                    s.name_in_search,
                    s.confidence,
                    s.url,
                ]
            )


def _select_tickers(
    settings_tickers: list[TickerEntry], filter_csv: str | None
) -> list[TickerEntry]:
    if not filter_csv:
        return settings_tickers
    wanted = {t.strip().upper() for t in filter_csv.split(",") if t.strip()}
    return [t for t in settings_tickers if t.ticker.upper() in wanted]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Suggest e_disclosure_id values for tickers.yaml entries"
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=ROOT / "config",
        help="Path to the directory holding tickers.yaml (default: ./config)",
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated subset of tickers to look up (default: all)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional CSV output path (in addition to stdout)",
    )
    return parser.parse_args(argv)


async def _main_async(argv: list[str]) -> int:
    args = _parse_args(argv)
    settings = load_all(args.config_dir)
    chosen = _select_tickers(settings.tickers.tickers, args.tickers)
    if not chosen:
        print("No tickers selected.", file=sys.stderr)
        return 2

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
        suggestions = await find_ids(client, chosen)

    print(format_table(suggestions))
    if args.out:
        write_csv(args.out, suggestions)
        print(f"\nCSV written to {args.out}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main_async(list(argv) if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
