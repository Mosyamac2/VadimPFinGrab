"""Database / migrations / transactions."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from edx.storage import Database


def _table_names(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row["name"] for row in cursor}


def test_migrate_creates_all_tables(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.sqlite")
    applied = db.migrate()
    assert "0001_init" in applied
    assert "0007_publications_period" in applied
    with closing(db.connect()) as conn:
        names = _table_names(conn)
        publications_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(publications)")
        }
    assert {
        "tickers",
        "publications",
        "documents",
        "metrics",
        "events",
        "runs",
        "schema_migrations",
    }.issubset(names)
    # Patch 17 columns must exist on publications after migrate().
    assert {
        "report_type_code",
        "report_type_label",
        "reporting_period_year",
        "reporting_period_type",
    }.issubset(publications_cols)


def test_migrate_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.sqlite")
    first = db.migrate()
    second = db.migrate()
    assert first[0] == "0001_init"
    assert "0002_classifier" in first
    assert second == []


def test_foreign_keys_enforced(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    with closing(db.connect()) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO publications (
                publication_id, ticker, publication_type, publication_date,
                source_url, status, discovered_at, updated_at
            ) VALUES ('p1', 'NOPE', 'report', '2026-01-01', 'http://x', 'discovered',
                      '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """
        )


def test_documents_cascade_on_publication_delete(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    with closing(db.connect()) as conn:
        conn.execute(
            "INSERT INTO tickers (ticker, e_disclosure_id, name, added_at) "
            "VALUES ('SBER', '1', 'Sberbank', '2026-01-01')"
        )
        conn.execute(
            """
            INSERT INTO publications (
                publication_id, ticker, publication_type, publication_date,
                source_url, status, discovered_at, updated_at
            ) VALUES ('pub-1', 'SBER', 'report', '2026-01-01', 'http://x',
                      'discovered', '2026-01-01', '2026-01-01')
            """
        )
        conn.execute(
            "INSERT INTO documents (publication_id, relative_path, file_hash) "
            "VALUES ('pub-1', 'a.pdf', 'h1')"
        )
        conn.execute("DELETE FROM publications WHERE publication_id = 'pub-1'")
        cursor = conn.execute("SELECT COUNT(*) AS c FROM documents")
        assert cursor.fetchone()["c"] == 0


def test_transaction_commits_on_success(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    with closing(db.connect()) as conn:
        with db.transaction(conn):
            conn.execute(
                "INSERT INTO tickers (ticker, e_disclosure_id, name, added_at) "
                "VALUES ('X', '1', 'X Inc', '2026-01-01')"
            )
        cursor = conn.execute("SELECT COUNT(*) AS c FROM tickers")
        assert cursor.fetchone()["c"] == 1


def test_transaction_rolls_back_on_exception(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    with closing(db.connect()) as conn:
        with pytest.raises(RuntimeError), db.transaction(conn):
            conn.execute(
                "INSERT INTO tickers (ticker, e_disclosure_id, name, added_at) "
                "VALUES ('Y', '2', 'Y Inc', '2026-01-01')"
            )
            raise RuntimeError("boom")
        cursor = conn.execute("SELECT COUNT(*) AS c FROM tickers")
        assert cursor.fetchone()["c"] == 0


def test_status_check_constraint_rejects_unknown(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    with closing(db.connect()) as conn:
        conn.execute(
            "INSERT INTO tickers (ticker, e_disclosure_id, name, added_at) "
            "VALUES ('A', '1', 'A', '2026-01-01')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO publications (
                    publication_id, ticker, publication_type, publication_date,
                    source_url, status, discovered_at, updated_at
                ) VALUES ('p9', 'A', 'report', '2026-01-01', 'http://x',
                          'mystery', '2026-01-01', '2026-01-01')
                """
            )
