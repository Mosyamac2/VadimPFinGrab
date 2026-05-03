"""Self-evolution bookkeeping repository (Patch 38).

The pipeline core (Discoverer/Classifier/MetricExtractor/...) is unaware of
this layer. It exists purely so that the optional ``edx evolve`` CLI
subcommand and its systemd timer can persist tick history and a skiplist
of companies that should not be retried.
"""

from __future__ import annotations

import sqlite3
from typing import Final

from edx.storage.db import Database, now_iso
from edx.storage.models import (
    EvolutionPhase,
    EvolutionSkiplistEntry,
    EvolutionTickRow,
    EvolutionVerdict,
)

GIVE_UP_THRESHOLD: Final[int] = 10


class EvolutionRepo:
    """CRUD over ``evolution_ticks`` and ``evolution_skiplist``."""

    def __init__(self, db: Database, conn: sqlite3.Connection) -> None:
        self.db = db
        self.conn = conn

    # ------------------------------------------------------------------ ticks

    def create_tick(
        self,
        *,
        started_at: str,
        phase: EvolutionPhase,
        batch_json: str,
    ) -> int:
        with self.db.transaction(self.conn):
            cursor = self.conn.execute(
                """
                INSERT INTO evolution_ticks (started_at, phase, batch_json)
                VALUES (?, ?, ?)
                """,
                (started_at, phase, batch_json),
            )
            tick_id = cursor.lastrowid
        if tick_id is None:
            raise RuntimeError("failed to insert evolution_ticks row")
        return int(tick_id)

    def update_tick(
        self,
        tick_id: int,
        *,
        phase: EvolutionPhase | None = None,
        verdict: EvolutionVerdict | None = None,
        snaps_before_json: str | None = None,
        snaps_after_json: str | None = None,
        verdicts_json: str | None = None,
        claude_session: str | None = None,
        claude_cost_usd: float | None = None,
        claude_turns: int | None = None,
        commit_sha: str | None = None,
        bundle_path: str | None = None,
        error_summary: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        """Partial update — only non-None fields are written.

        SQL is built dynamically so that callers can pass exactly the
        subset they want to change without zeroing everything else.
        """
        sets: list[str] = []
        values: list[object] = []
        if phase is not None:
            sets.append("phase = ?")
            values.append(phase)
        if verdict is not None:
            sets.append("verdict = ?")
            values.append(verdict)
        if snaps_before_json is not None:
            sets.append("snaps_before_json = ?")
            values.append(snaps_before_json)
        if snaps_after_json is not None:
            sets.append("snaps_after_json = ?")
            values.append(snaps_after_json)
        if verdicts_json is not None:
            sets.append("verdicts_json = ?")
            values.append(verdicts_json)
        if claude_session is not None:
            sets.append("claude_session = ?")
            values.append(claude_session)
        if claude_cost_usd is not None:
            sets.append("claude_cost_usd = ?")
            values.append(claude_cost_usd)
        if claude_turns is not None:
            sets.append("claude_turns = ?")
            values.append(claude_turns)
        if commit_sha is not None:
            sets.append("commit_sha = ?")
            values.append(commit_sha)
        if bundle_path is not None:
            sets.append("bundle_path = ?")
            values.append(bundle_path)
        if error_summary is not None:
            sets.append("error_summary = ?")
            values.append(error_summary)
        if finished_at is not None:
            sets.append("finished_at = ?")
            values.append(finished_at)
        if not sets:
            return
        values.append(tick_id)
        with self.db.transaction(self.conn):
            self.conn.execute(
                f"UPDATE evolution_ticks SET {', '.join(sets)} WHERE tick_id = ?",
                values,
            )

    def get_tick(self, tick_id: int) -> EvolutionTickRow | None:
        cursor = self.conn.execute(
            "SELECT * FROM evolution_ticks WHERE tick_id = ?",
            (tick_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_tick(row)

    def latest_ticks(self, limit: int) -> list[EvolutionTickRow]:
        cursor = self.conn.execute(
            "SELECT * FROM evolution_ticks ORDER BY tick_id DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_tick(row) for row in cursor]

    def daily_cost_usd(self, day: str) -> float:
        """Sum of ``claude_cost_usd`` for ticks finished on ``day`` (YYYY-MM-DD).

        Compares the leading 10 chars of ``finished_at`` so the call works
        regardless of timestamp suffix (Z / +00:00 / seconds resolution).
        """
        cursor = self.conn.execute(
            """
            SELECT COALESCE(SUM(claude_cost_usd), 0.0) AS total
              FROM evolution_ticks
             WHERE finished_at IS NOT NULL
               AND substr(finished_at, 1, 10) = ?
            """,
            (day,),
        )
        row = cursor.fetchone()
        return float(row["total"]) if row is not None else 0.0

    # --------------------------------------------------------------- skiplist

    def get_skiplist(self) -> list[EvolutionSkiplistEntry]:
        cursor = self.conn.execute(
            "SELECT * FROM evolution_skiplist ORDER BY company_id"
        )
        return [_row_to_skip(row) for row in cursor]

    def is_in_skiplist(self, company_id: str) -> bool:
        cursor = self.conn.execute(
            "SELECT 1 FROM evolution_skiplist WHERE company_id = ?",
            (company_id,),
        )
        return cursor.fetchone() is not None

    def get_skiplist_entry(
        self, company_id: str
    ) -> EvolutionSkiplistEntry | None:
        cursor = self.conn.execute(
            "SELECT * FROM evolution_skiplist WHERE company_id = ?",
            (company_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_skip(row)

    def bump_failure(self, company_id: str, last_tick_id: int) -> int:
        """Increment ``failure_count`` for ``company_id``.

        - If the company is not in the skiplist yet, insert with
          ``reason='give_up'`` and ``failure_count=1``.
        - At ``failure_count >= GIVE_UP_THRESHOLD`` (10) the counter is
          clamped — repeated bumps are idempotent (still ``give_up``).

        Returns the new ``failure_count``.
        """
        with self.db.transaction(self.conn):
            existing = self.conn.execute(
                "SELECT failure_count, reason FROM evolution_skiplist "
                "WHERE company_id = ?",
                (company_id,),
            ).fetchone()
            if existing is None:
                new_count = 1
                self.conn.execute(
                    """
                    INSERT INTO evolution_skiplist
                        (company_id, reason, failure_count, last_tick_id, updated_at)
                    VALUES (?, 'give_up', ?, ?, ?)
                    """,
                    (company_id, new_count, last_tick_id, now_iso()),
                )
            else:
                current = int(existing["failure_count"])
                new_count = min(current + 1, GIVE_UP_THRESHOLD)
                self.conn.execute(
                    """
                    UPDATE evolution_skiplist
                       SET failure_count = ?,
                           last_tick_id  = ?,
                           updated_at    = ?,
                           reason        = CASE
                               WHEN reason = 'manual_blacklist' THEN 'manual_blacklist'
                               WHEN reason = 'moex_overlap'    THEN 'moex_overlap'
                               ELSE 'give_up'
                           END
                     WHERE company_id = ?
                    """,
                    (new_count, last_tick_id, now_iso(), company_id),
                )
        return new_count

    def add_overlap(self, company_id: str) -> None:
        """Mark a company as MOEX-overlap (handled by the main pipeline).

        Idempotent: existing rows keep their reason and counter intact.
        """
        with self.db.transaction(self.conn):
            self.conn.execute(
                """
                INSERT OR IGNORE INTO evolution_skiplist
                    (company_id, reason, failure_count, updated_at)
                VALUES (?, 'moex_overlap', 0, ?)
                """,
                (company_id, now_iso()),
            )

    def add_manual_blacklist(self, company_id: str) -> None:
        """Operator-driven block. Idempotent."""
        with self.db.transaction(self.conn):
            self.conn.execute(
                """
                INSERT OR REPLACE INTO evolution_skiplist
                    (company_id, reason, failure_count, last_tick_id, updated_at)
                VALUES (
                    ?,
                    'manual_blacklist',
                    COALESCE(
                        (SELECT failure_count FROM evolution_skiplist
                         WHERE company_id = ?),
                        0
                    ),
                    (SELECT last_tick_id FROM evolution_skiplist
                     WHERE company_id = ?),
                    ?
                )
                """,
                (company_id, company_id, company_id, now_iso()),
            )

    def reset(self, company_id: str) -> bool:
        """Remove an entry from the skiplist.

        Returns ``True`` when a row was actually deleted, ``False`` otherwise.
        """
        with self.db.transaction(self.conn):
            cursor = self.conn.execute(
                "DELETE FROM evolution_skiplist WHERE company_id = ?",
                (company_id,),
            )
        return cursor.rowcount > 0


def _row_to_tick(row: sqlite3.Row) -> EvolutionTickRow:
    return EvolutionTickRow(
        tick_id=row["tick_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        phase=row["phase"],
        verdict=row["verdict"],
        batch_json=row["batch_json"],
        snaps_before_json=row["snaps_before_json"],
        snaps_after_json=row["snaps_after_json"],
        verdicts_json=row["verdicts_json"],
        claude_session=row["claude_session"],
        claude_cost_usd=row["claude_cost_usd"],
        claude_turns=row["claude_turns"],
        commit_sha=row["commit_sha"],
        bundle_path=row["bundle_path"],
        error_summary=row["error_summary"],
    )


def _row_to_skip(row: sqlite3.Row) -> EvolutionSkiplistEntry:
    return EvolutionSkiplistEntry(
        company_id=row["company_id"],
        reason=row["reason"],
        failure_count=int(row["failure_count"]),
        last_tick_id=row["last_tick_id"],
        updated_at=row["updated_at"],
    )
