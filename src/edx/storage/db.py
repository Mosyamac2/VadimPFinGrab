"""SQLite state database: connection, migrations, transactions.

Lightweight layer over :mod:`sqlite3` — no ORM. Each repository accepts a
:class:`sqlite3.Connection` and uses :meth:`Database.transaction` for atomic
logical actions.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from edx.logging_setup import get_logger

DEFAULT_MIGRATIONS_DIR: Final[Path] = Path(__file__).parent / "migrations"
SCHEMA_MIGRATIONS_TABLE: Final[str] = "schema_migrations"


def now_iso() -> str:
    """ISO-8601 UTC timestamp with seconds resolution."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _split_sql_statements(text: str) -> list[str]:
    """Naive SQL splitter for our DDL-only migration files.

    Strips ``--`` line comments and splits on ``;``. Sufficient for the schema
    migrations we author by hand; do not feed it user-supplied SQL.
    """
    cleaned_lines = []
    for raw_line in text.splitlines():
        comment_start = raw_line.find("--")
        line = raw_line if comment_start < 0 else raw_line[:comment_start]
        cleaned_lines.append(line)
    blob = "\n".join(cleaned_lines)
    return [stmt.strip() for stmt in blob.split(";") if stmt.strip()]


class Database:
    """Owns a path to a SQLite file and knows how to migrate / open it."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        """Open a fresh connection. Caller is responsible for closing it.

        Sets ``foreign_keys = ON`` and (for file-backed databases) WAL mode.
        ``isolation_level = None`` so we can manage transactions explicitly.
        """
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if str(self.path) != ":memory:":
            with contextlib.suppress(sqlite3.DatabaseError):
                conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def transaction(
        self, conn: sqlite3.Connection
    ) -> Iterator[sqlite3.Connection]:
        """Run a block atomically. Rolls back on any exception."""
        conn.execute("BEGIN")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def migrate(
        self, migrations_dir: Path | None = None
    ) -> list[str]:
        """Apply pending migration files. Returns the versions applied this call."""
        log = get_logger("edx.storage.db")
        directory = migrations_dir or DEFAULT_MIGRATIONS_DIR
        applied: list[str] = []

        conn = self.connect()
        try:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} ("
                "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            cursor = conn.execute(
                f"SELECT version FROM {SCHEMA_MIGRATIONS_TABLE}"
            )
            already = {row["version"] for row in cursor}

            for sql_file in sorted(directory.glob("*.sql")):
                version = sql_file.stem
                if version in already:
                    continue
                statements = _split_sql_statements(
                    sql_file.read_text(encoding="utf-8")
                )
                with self.transaction(conn):
                    for stmt in statements:
                        conn.execute(stmt)
                    conn.execute(
                        f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} "
                        "(version, applied_at) VALUES (?, ?)",
                        (version, now_iso()),
                    )
                applied.append(version)
                log.info("migration_applied", version=version, file=sql_file.name)

            if not applied:
                log.info("migrations_up_to_date")
        finally:
            conn.close()

        return applied
