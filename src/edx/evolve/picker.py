"""Picker — выбор батча из 3 компаний для одного evolve-тика (Patch 39).

Алгоритм детерминирован: при равенстве приоритетов сортирует по
``company_id`` ASC. Это критично для тестов и для воспроизводимости
тиков (если на тике произошёл сбой, replay'ом его можно пройти заново).

Приоритет (lower = higher):
  0. Never attempted — нет ни одного тика с этим company_id.
  1. Failed recoverable — последний verdict ∈ {fail, regression,
     regression_*} И не в skiplist И failure_count < 3.
  2. OK с истёкшим cooldown (последний `ok` финиш > N дней назад).

Excluded:
  - skiplist с reason ∈ {give_up, manual_blacklist}.
  - moex_overlap → автоматически добавляется в skiplist на лету
    (single-shot insert, далее silent skip).
  - последний verdict='ok' AND finished_at в пределах cooldown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from edx.evolve.csv_loader import CompanyRow
from edx.storage.repositories.evolution_repo import EvolutionRepo

DEFAULT_BATCH_SIZE: Final[int] = 3
DEFAULT_COOLDOWN_DAYS: Final[int] = 7

_PRIORITY_NEVER = 0
_PRIORITY_FAILED = 1
_PRIORITY_OK_COOLDOWN_OVER = 2
_PRIORITY_EXCLUDED = 99


@dataclass(frozen=True, slots=True)
class PickerInput:
    companies: list[CompanyRow]
    moex_e_disclosure_ids: frozenset[str] = field(default_factory=frozenset)
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS
    batch_size: int = DEFAULT_BATCH_SIZE
    today_iso: str = ""
    """If empty, current UTC is used. Tests pin this for determinism."""


@dataclass(frozen=True, slots=True)
class _LatestForCompany:
    """The most recent finished tick for a company (or None)."""

    verdict: str | None
    finished_at: str | None


def pick_next_batch(
    inp: PickerInput, repo: EvolutionRepo
) -> list[CompanyRow]:
    """Return up to ``inp.batch_size`` companies in priority order.

    Side effects:
      - Companies whose ``company_id`` is in ``inp.moex_e_disclosure_ids``
        are added to the skiplist with reason ``moex_overlap`` on the
        very first call (idempotent thereafter).
    """
    if inp.batch_size <= 0:
        return []

    # 1. mark MOEX-overlap companies in the skiplist (idempotent).
    moex_set = inp.moex_e_disclosure_ids
    seen_ids: set[str] = set()
    for c in inp.companies:
        if c.company_id in moex_set and c.company_id not in seen_ids:
            seen_ids.add(c.company_id)
            repo.add_overlap(c.company_id)

    skiplist_ids = frozenset(e.company_id for e in repo.get_skiplist())
    latest = _load_latest_per_company(repo)
    today = (
        datetime.fromisoformat(inp.today_iso)
        if inp.today_iso
        else datetime.now(UTC)
    )
    cooldown_threshold = today - timedelta(days=inp.cooldown_days)

    candidates: list[tuple[int, str, CompanyRow]] = []
    for company in inp.companies:
        priority = _priority_for(
            company,
            skiplist_ids=skiplist_ids,
            latest=latest.get(company.company_id),
            cooldown_threshold=cooldown_threshold,
        )
        if priority == _PRIORITY_EXCLUDED:
            continue
        candidates.append((priority, company.company_id, company))

    # Tiebreaker: company_id ASC.
    candidates.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in candidates[: inp.batch_size]]


def _priority_for(
    company: CompanyRow,
    *,
    skiplist_ids: frozenset[str],
    latest: _LatestForCompany | None,
    cooldown_threshold: datetime,
) -> int:
    if company.company_id in skiplist_ids:
        return _PRIORITY_EXCLUDED

    if latest is None or latest.verdict is None:
        return _PRIORITY_NEVER

    verdict = latest.verdict
    if verdict in {"fail", "regression", "regression_tests", "regression_canary"}:
        return _PRIORITY_FAILED

    if verdict == "ok" and latest.finished_at:
        try:
            finished = datetime.fromisoformat(latest.finished_at)
        except ValueError:
            return _PRIORITY_NEVER
        if finished < cooldown_threshold:
            return _PRIORITY_OK_COOLDOWN_OVER
        return _PRIORITY_EXCLUDED

    # Unknown / interim verdicts (neutral / flaky / give_up / skipped_budget):
    # treat like "never attempted" so we don't get stuck on a transient state.
    return _PRIORITY_NEVER


def _load_latest_per_company(
    repo: EvolutionRepo,
) -> dict[str, _LatestForCompany]:
    """Group ticks by company_id, keep the latest finished verdict.

    We parse ``batch_json`` (a JSON array of {company_id, ticker, ...})
    and assign the tick's verdict to every company in its batch.
    """
    cursor = repo.conn.execute(
        "SELECT batch_json, verdict, finished_at FROM evolution_ticks "
        "ORDER BY tick_id ASC"
    )
    latest: dict[str, _LatestForCompany] = {}
    for row in cursor:
        try:
            batch = json.loads(row["batch_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(batch, list):
            continue
        verdict = row["verdict"]
        finished_at = row["finished_at"]
        for entry in batch:
            if not isinstance(entry, dict):
                continue
            cid = entry.get("company_id")
            if not isinstance(cid, str) or not cid:
                continue
            # Later ticks overwrite earlier — we iterate in tick_id ASC.
            latest[cid] = _LatestForCompany(
                verdict=verdict, finished_at=finished_at
            )
    return latest


__all__ = ["PickerInput", "pick_next_batch"]
