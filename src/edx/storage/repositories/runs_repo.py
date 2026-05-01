"""Pipeline runs journal."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from edx.storage.db import Database, now_iso
from edx.storage.models import RunMode, RunRow, RunStatus


class RunsRepo:
    def __init__(self, db: Database, conn: sqlite3.Connection) -> None:
        self.db = db
        self.conn = conn

    def start_run(self, mode: RunMode) -> int:
        with self.db.transaction(self.conn):
            cursor = self.conn.execute(
                """
                INSERT INTO runs (started_at, status, mode)
                VALUES (?, 'running', ?)
                """,
                (now_iso(), mode),
            )
            run_id = cursor.lastrowid
        if run_id is None:
            raise RuntimeError("failed to insert run row")
        return int(run_id)

    def finish_run(
        self,
        run_id: int,
        *,
        status: RunStatus,
        stats: dict[str, Any] | None = None,
        error_summary: str | None = None,
    ) -> None:
        with self.db.transaction(self.conn):
            self.conn.execute(
                """
                UPDATE runs
                   SET finished_at   = ?,
                       status        = ?,
                       stats_json    = ?,
                       error_summary = ?
                 WHERE run_id = ?
                """,
                (
                    now_iso(),
                    status,
                    json.dumps(stats, ensure_ascii=False) if stats is not None else None,
                    error_summary,
                    run_id,
                ),
            )

    def get_by_id(self, run_id: int) -> RunRow | None:
        cursor = self.conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_run(row)

    def latest(self, limit: int = 5) -> list[RunRow]:
        cursor = self.conn.execute(
            "SELECT * FROM runs ORDER BY run_id DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_run(row) for row in cursor]


def _row_to_run(row: sqlite3.Row) -> RunRow:
    return RunRow(
        run_id=row["run_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        mode=row["mode"],
        stats_json=row["stats_json"],
        error_summary=row["error_summary"],
    )
