"""snapshot_ticker / snapshot_batch."""

from __future__ import annotations

import sqlite3

from edx.evolve.snapshot import snapshot_batch, snapshot_ticker
from edx.storage import Database


def _seed_ticker(conn: sqlite3.Connection, ticker: str) -> None:
    """Insert a minimal ticker row so FKs hold for publications."""
    conn.execute(
        "INSERT INTO tickers (ticker, e_disclosure_id, name, added_at) "
        "VALUES (?, ?, ?, ?)",
        (ticker, ticker.replace("EDX", ""), f"Co {ticker}", "2026-05-03T00:00:00"),
    )


def _seed_publication(
    conn: sqlite3.Connection,
    *,
    pub_id: str,
    ticker: str,
    status: str,
    publication_date: str,
) -> None:
    conn.execute(
        """
        INSERT INTO publications (
            publication_id, ticker, publication_type, publication_date,
            source_url, status, discovered_at, updated_at
        ) VALUES (?, ?, 'report', ?, ?, ?, ?, ?)
        """,
        (
            pub_id,
            ticker,
            publication_date,
            f"https://example.com/{pub_id}",
            status,
            publication_date,
            publication_date,
        ),
    )


def test_snapshot_empty_ticker(
    evolve_db: Database, evolve_conn: sqlite3.Connection
) -> None:
    snap = snapshot_ticker(evolve_conn, "EDX_NONE")
    assert snap.ticker == "EDX_NONE"
    assert snap.publications_total == 0
    assert snap.publications_by_status == {}
    assert snap.documents_total == 0
    assert snap.metrics_rows == 0
    assert snap.metrics_by_standard == {}
    assert snap.qa_issues_count == 0
    assert snap.qa_issues_codes == {}
    assert snap.last_publication_date is None


def test_snapshot_publications_aggregate(
    evolve_db: Database, evolve_conn: sqlite3.Connection
) -> None:
    _seed_ticker(evolve_conn, "EDX1210")
    _seed_publication(
        evolve_conn,
        pub_id="P1",
        ticker="EDX1210",
        status="written",
        publication_date="2026-04-01",
    )
    _seed_publication(
        evolve_conn,
        pub_id="P2",
        ticker="EDX1210",
        status="written",
        publication_date="2026-04-02",
    )
    _seed_publication(
        evolve_conn,
        pub_id="P3",
        ticker="EDX1210",
        status="failed",
        publication_date="2026-04-03",
    )

    snap = snapshot_ticker(evolve_conn, "EDX1210")
    assert snap.publications_total == 3
    assert snap.publications_by_status == {"written": 2, "failed": 1}
    assert snap.last_publication_date == "2026-04-03"


def test_snapshot_metrics_and_qa(
    evolve_db: Database, evolve_conn: sqlite3.Connection
) -> None:
    _seed_ticker(evolve_conn, "EDX1210")
    _seed_publication(
        evolve_conn,
        pub_id="P1",
        ticker="EDX1210",
        status="written",
        publication_date="2026-04-01",
    )
    evolve_conn.execute(
        """
        INSERT INTO metrics (ticker, reporting_date, period_type, reporting_standard,
                             metric_name, value, currency, unit, extracted_at)
        VALUES ('EDX1210','2026-03-31','Q1','IFRS','revenue', 100, 'RUB','RUB','2026-04-02'),
               ('EDX1210','2026-03-31','Q1','IFRS','net_income', 10, 'RUB','RUB','2026-04-02'),
               ('EDX1210','2026-03-31','Q1','RSBU','revenue', 99, 'RUB','RUB','2026-04-02')
        """
    )
    evolve_conn.execute(
        """
        INSERT INTO qa_issues (publication_id, ticker, code, message, created_at)
        VALUES ('P1','EDX1210','incomplete','low coverage','2026-04-02'),
               ('P1','EDX1210','suspicious_yoy','spike','2026-04-02')
        """
    )

    snap = snapshot_ticker(evolve_conn, "EDX1210")
    assert snap.metrics_rows == 3
    assert snap.metrics_by_standard == {"IFRS": 2, "RSBU": 1}
    assert snap.qa_issues_count == 2
    assert snap.qa_issues_codes == {"incomplete": 1, "suspicious_yoy": 1}


def test_snapshot_batch_returns_each_ticker(
    evolve_db: Database, evolve_conn: sqlite3.Connection
) -> None:
    _seed_ticker(evolve_conn, "EDX1")
    _seed_ticker(evolve_conn, "EDX2")
    snaps = snapshot_batch(evolve_conn, ["EDX1", "EDX2"])
    assert set(snaps.keys()) == {"EDX1", "EDX2"}
    assert snaps["EDX1"].publications_total == 0
    assert snaps["EDX2"].publications_total == 0
