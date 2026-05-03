"""Picker: batch selection logic, MOEX-overlap auto-skiplist, cooldown."""

from __future__ import annotations

import json

from edx.evolve.csv_loader import CompanyRow
from edx.evolve.picker import PickerInput, pick_next_batch
from edx.storage import EvolutionRepo


def _company(cid: str, name: str = "x") -> CompanyRow:
    return CompanyRow(company_id=cid, name=name, type="non_bank")


def _create_finished_tick(
    repo: EvolutionRepo,
    *,
    company_ids: list[str],
    verdict: str,
    finished_at: str,
    started_at: str | None = None,
) -> int:
    started = started_at or finished_at
    batch = [{"company_id": cid, "ticker": f"EDX{cid}"} for cid in company_ids]
    tid = repo.create_tick(
        started_at=started,
        phase="baseline",
        batch_json=json.dumps(batch, ensure_ascii=False),
    )
    repo.update_tick(
        tid,
        phase="done",
        verdict=verdict,  # type: ignore[arg-type]
        finished_at=finished_at,
    )
    return tid


def test_picker_picks_3_never_attempted(evolve_repo: EvolutionRepo) -> None:
    companies = [_company(f"{i}") for i in (5, 1, 3, 4, 2)]
    out = pick_next_batch(
        PickerInput(companies=companies, today_iso="2026-05-03T10:00:00+00:00"),
        evolve_repo,
    )
    assert [c.company_id for c in out] == ["1", "2", "3"]


def test_picker_skips_moex_overlap_and_marks_skiplist(
    evolve_repo: EvolutionRepo,
) -> None:
    companies = [_company(f"{i}") for i in (1, 2, 3, 4, 5)]
    out = pick_next_batch(
        PickerInput(
            companies=companies,
            moex_e_disclosure_ids=frozenset({"1", "3"}),
            today_iso="2026-05-03T10:00:00+00:00",
        ),
        evolve_repo,
    )
    assert [c.company_id for c in out] == ["2", "4", "5"]
    skip_ids = {e.company_id for e in evolve_repo.get_skiplist()}
    assert {"1", "3"}.issubset(skip_ids)


def test_picker_skips_give_up(evolve_repo: EvolutionRepo) -> None:
    """Bump company 2 up to GIVE_UP_THRESHOLD strikes → picker excludes it."""
    from edx.storage.repositories.evolution_repo import GIVE_UP_THRESHOLD

    companies = [_company(f"{i}") for i in (1, 2, 3, 4, 5)]
    tid = evolve_repo.create_tick(
        started_at="2026-05-01T10:00:00+00:00", phase="baseline", batch_json="[]"
    )
    for _ in range(GIVE_UP_THRESHOLD):
        evolve_repo.bump_failure("2", tid)
    out = pick_next_batch(
        PickerInput(companies=companies, today_iso="2026-05-03T10:00:00+00:00"),
        evolve_repo,
    )
    assert "2" not in [c.company_id for c in out]


def test_picker_does_NOT_skip_below_give_up_threshold(
    evolve_repo: EvolutionRepo,
) -> None:
    """Anti-regression for production bug: bump_failure() inserts a row
    on the FIRST strike. Picker must keep picking until failure_count >=
    GIVE_UP_THRESHOLD — otherwise companies are blocked permanently
    after one fail."""
    companies = [_company(f"{i}") for i in (1, 2, 3, 4, 5)]
    tid = evolve_repo.create_tick(
        started_at="2026-05-01T10:00:00+00:00", phase="baseline", batch_json="[]"
    )
    # Single bump — count=1, BELOW threshold.
    evolve_repo.bump_failure("2", tid)
    out = pick_next_batch(
        PickerInput(companies=companies, today_iso="2026-05-03T10:00:00+00:00"),
        evolve_repo,
    )
    # Company 2 is still picked (priority _PRIORITY_FAILED is highest
    # of the non-NEVER pool — it'll come AFTER never-attempted ones).
    assert "2" in [c.company_id for c in out]


def test_picker_does_NOT_skip_at_failure_count_two(
    evolve_repo: EvolutionRepo,
) -> None:
    """Two strikes — still NOT in skiplist territory."""
    companies = [_company(f"{i}") for i in (1, 2, 3, 4, 5)]
    tid = evolve_repo.create_tick(
        started_at="2026-05-01T10:00:00+00:00", phase="baseline", batch_json="[]"
    )
    evolve_repo.bump_failure("2", tid)
    evolve_repo.bump_failure("2", tid)
    out = pick_next_batch(
        PickerInput(companies=companies, today_iso="2026-05-03T10:00:00+00:00"),
        evolve_repo,
    )
    assert "2" in [c.company_id for c in out]


def test_picker_priority_failed_over_ok_cooldown(
    evolve_repo: EvolutionRepo,
) -> None:
    """A failed company always beats an OK-cooldown-expired one."""
    companies = [_company("1"), _company("2"), _company("3")]
    # 1: never attempted (priority 0)
    # 2: failed (priority 1)
    _create_finished_tick(
        evolve_repo,
        company_ids=["2"],
        verdict="fail",
        finished_at="2026-04-01T10:00:00+00:00",
    )
    # 3: OK with cooldown expired (priority 2)
    _create_finished_tick(
        evolve_repo,
        company_ids=["3"],
        verdict="ok",
        finished_at="2026-04-01T10:00:00+00:00",
    )
    out = pick_next_batch(
        PickerInput(
            companies=companies,
            cooldown_days=7,
            today_iso="2026-05-03T10:00:00+00:00",
        ),
        evolve_repo,
    )
    assert [c.company_id for c in out] == ["1", "2", "3"]


def test_picker_cooldown_excludes_recent_ok(
    evolve_repo: EvolutionRepo,
) -> None:
    companies = [_company("1"), _company("2")]
    # 1: OK 6 days ago at cooldown=7 → excluded
    _create_finished_tick(
        evolve_repo,
        company_ids=["1"],
        verdict="ok",
        finished_at="2026-04-27T10:00:00+00:00",
    )
    out = pick_next_batch(
        PickerInput(
            companies=companies,
            cooldown_days=7,
            today_iso="2026-05-03T10:00:00+00:00",
            batch_size=3,
        ),
        evolve_repo,
    )
    assert [c.company_id for c in out] == ["2"]


def test_picker_cooldown_includes_old_ok(
    evolve_repo: EvolutionRepo,
) -> None:
    companies = [_company("1"), _company("2")]
    # 1: OK 8 days ago at cooldown=7 → included as priority 2.
    _create_finished_tick(
        evolve_repo,
        company_ids=["1"],
        verdict="ok",
        finished_at="2026-04-25T10:00:00+00:00",
    )
    out = pick_next_batch(
        PickerInput(
            companies=companies,
            cooldown_days=7,
            today_iso="2026-05-03T10:00:00+00:00",
        ),
        evolve_repo,
    )
    assert [c.company_id for c in out] == ["2", "1"]  # never_attempted first


def test_picker_returns_empty_when_no_candidates(
    evolve_repo: EvolutionRepo,
) -> None:
    companies = [_company("1"), _company("2")]
    evolve_repo.add_overlap("1")
    evolve_repo.add_manual_blacklist("2")
    out = pick_next_batch(
        PickerInput(companies=companies, today_iso="2026-05-03T10:00:00+00:00"),
        evolve_repo,
    )
    assert out == []


def test_picker_deterministic_order(evolve_repo: EvolutionRepo) -> None:
    """Same input → same output, every call."""
    companies = [_company(f"{i}") for i in range(10, 0, -1)]
    seen: set[tuple[str, ...]] = set()
    for _ in range(5):
        out = pick_next_batch(
            PickerInput(
                companies=companies, today_iso="2026-05-03T10:00:00+00:00"
            ),
            evolve_repo,
        )
        seen.add(tuple(c.company_id for c in out))
    assert len(seen) == 1


def test_picker_batch_size_limit(evolve_repo: EvolutionRepo) -> None:
    companies = [_company(f"{i}") for i in (1, 2, 3, 4, 5)]
    out = pick_next_batch(
        PickerInput(
            companies=companies,
            batch_size=2,
            today_iso="2026-05-03T10:00:00+00:00",
        ),
        evolve_repo,
    )
    assert [c.company_id for c in out] == ["1", "2"]


def test_picker_zero_batch_returns_empty(evolve_repo: EvolutionRepo) -> None:
    companies = [_company("1")]
    out = pick_next_batch(
        PickerInput(
            companies=companies,
            batch_size=0,
            today_iso="2026-05-03T10:00:00+00:00",
        ),
        evolve_repo,
    )
    assert out == []
