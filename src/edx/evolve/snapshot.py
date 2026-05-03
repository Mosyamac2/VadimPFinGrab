"""Per-ticker state snapshots for evolve verdict computation (Patch 40).

A snapshot is a deterministic, JSON-serialisable summary of one
ticker's footprint in ``state.sqlite``. It's taken before a pipeline
run and after it; the difference drives the verdict (improved /
neutral / regressed / failed).
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TickerSnapshot:
    ticker: str
    publications_total: int
    publications_by_status: dict[str, int]
    documents_total: int
    metrics_rows: int
    metrics_by_standard: dict[str, int]
    qa_issues_count: int
    qa_issues_codes: dict[str, int]
    last_publication_date: str | None

    def as_json_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Counts:
    by_status: dict[str, int] = field(default_factory=dict)


def snapshot_ticker(
    conn: sqlite3.Connection, ticker: str
) -> TickerSnapshot:
    """Read aggregate counters for one ticker. Pure read, no transactions."""

    publications_total, publications_by_status, last_pub_date = (
        _publications_stats(conn, ticker)
    )
    documents_total = _documents_total_for_ticker(conn, ticker)
    metrics_rows, metrics_by_standard = _metrics_stats(conn, ticker)
    qa_count, qa_codes = _qa_issues_stats(conn, ticker)

    return TickerSnapshot(
        ticker=ticker,
        publications_total=publications_total,
        publications_by_status=publications_by_status,
        documents_total=documents_total,
        metrics_rows=metrics_rows,
        metrics_by_standard=metrics_by_standard,
        qa_issues_count=qa_count,
        qa_issues_codes=qa_codes,
        last_publication_date=last_pub_date,
    )


def snapshot_batch(
    conn: sqlite3.Connection, tickers: list[str]
) -> dict[str, TickerSnapshot]:
    return {t: snapshot_ticker(conn, t) for t in tickers}


def _publications_stats(
    conn: sqlite3.Connection, ticker: str
) -> tuple[int, dict[str, int], str | None]:
    cursor = conn.execute(
        """
        SELECT status, COUNT(*) AS cnt, MAX(publication_date) AS max_date
          FROM publications
         WHERE ticker = ?
         GROUP BY status
        """,
        (ticker,),
    )
    by_status: dict[str, int] = {}
    total = 0
    last_date: str | None = None
    for row in cursor:
        by_status[row["status"]] = int(row["cnt"])
        total += int(row["cnt"])
        candidate = row["max_date"]
        if candidate and (last_date is None or candidate > last_date):
            last_date = candidate
    return total, by_status, last_date


def _documents_total_for_ticker(
    conn: sqlite3.Connection, ticker: str
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
          FROM documents d
          JOIN publications p ON p.publication_id = d.publication_id
         WHERE p.ticker = ?
        """,
        (ticker,),
    ).fetchone()
    return int(row["cnt"]) if row is not None else 0


def _metrics_stats(
    conn: sqlite3.Connection, ticker: str
) -> tuple[int, dict[str, int]]:
    cursor = conn.execute(
        """
        SELECT reporting_standard, COUNT(*) AS cnt
          FROM metrics
         WHERE ticker = ?
         GROUP BY reporting_standard
        """,
        (ticker,),
    )
    by_standard: dict[str, int] = {}
    total = 0
    for row in cursor:
        by_standard[row["reporting_standard"]] = int(row["cnt"])
        total += int(row["cnt"])
    return total, by_standard


def _qa_issues_stats(
    conn: sqlite3.Connection, ticker: str
) -> tuple[int, dict[str, int]]:
    cursor = conn.execute(
        """
        SELECT code, COUNT(*) AS cnt
          FROM qa_issues
         WHERE ticker = ?
         GROUP BY code
        """,
        (ticker,),
    )
    by_code: dict[str, int] = {}
    total = 0
    for row in cursor:
        by_code[row["code"]] = int(row["cnt"])
        total += int(row["cnt"])
    return total, by_code


__all__ = ["TickerSnapshot", "snapshot_batch", "snapshot_ticker"]
