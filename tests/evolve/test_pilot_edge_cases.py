"""Edge-case scenarios anticipated in pilot Phase 1/2 (Patch 45).

Each test corresponds to a class of incident that we expect to encounter
when the demon runs on a real VPS for 24h. Catching them up-front in
unit tests means the pilot can focus on cost/budget calibration rather
than first-time-ever bugs.
"""

from __future__ import annotations

import json
from pathlib import Path

from edx.evolve.csv_loader import CompanyRow
from edx.evolve.memory import has_new_entry_since, read
from edx.evolve.snapshot import TickerSnapshot
from edx.evolve.taxonomy import classify_failures
from edx.evolve.verdict import (
    TickerVerdict,
    aggregate_verdict,
    compute_verdict,
)

# -------------------------------------------------------------- edge cases


def test_two_failing_companies_same_class_classified_independently(
    tmp_path: Path,
) -> None:
    """Pilot sanity: when two batch-companies share a failure_class
    (e.g. both hit ServicePipe 403), the taxonomy must produce TWO
    entries — one per ticker — not collapse to a single shared entry.
    """
    log = tmp_path / "p.log"
    log.write_text(
        json.dumps(
            {
                "event": "discoverer_non_200",
                "level": "warning",
                "ticker": "EDX1",
                "status": 403,
            }
        )
        + "\n"
        + json.dumps(
            {
                "event": "discoverer_non_200",
                "level": "warning",
                "ticker": "EDX2",
                "status": 403,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = classify_failures(
        log,
        state_slice={"EDX1": {}, "EDX2": {}, "EDX3": {}},
        failing_tickers=["EDX1", "EDX2"],
    )
    assert [e.ticker for e in out] == ["EDX1", "EDX2"]
    for entry in out:
        assert entry.code == "discoverer_403_servicepipe"


def test_verdict_treats_zero_metrics_with_returncode_zero_as_ok() -> None:
    """All publications in terminal states (skipped) with 0 metrics → ``ok``.

    Originally written in pilot Phase 1 to ensure the verdict wasn't ``fail``
    (which would push companies to the skiplist after 3 ticks). The all-
    terminal-no-metrics fix (tick #180) now correctly returns ``ok`` instead of
    ``neutral``: the company has been fully processed, all publications were
    inspected by the metric extractor, none had IFRS/RSBU/ISSUER documents, so
    there is nothing more to extract.  ``ok`` places the ticker on the 7-day
    cooldown cycle instead of re-selecting it on every tick indefinitely.
    """
    snap = TickerSnapshot(
        ticker="EDXX",
        publications_total=1,
        publications_by_status={"skipped": 1},
        documents_total=0,
        metrics_rows=0,
        metrics_by_standard={},
        qa_issues_count=0,
        qa_issues_codes={},
        last_publication_date="2026-04-01",
    )
    v = compute_verdict(snap, snap, pipeline_returncode=0)
    assert v.code == "ok"


def test_aggregate_verdict_with_mixed_neutral_and_ok_is_neutral() -> None:
    """A batch where 1 company OK and 2 neutral should NOT trigger the
    happy-path xlsx merge as if all were OK — gate keys off `aggregate
    == "ok"`. Pilot must verify."""
    a = TickerVerdict(
        ticker="EDX1",
        code="ok",
        metrics_delta=3,
        publications_written_delta=1,
        qa_issues_delta=0,
        notes=(),
    )
    b = TickerVerdict(
        ticker="EDX2",
        code="neutral",
        metrics_delta=0,
        publications_written_delta=0,
        qa_issues_delta=0,
        notes=(),
    )
    c = TickerVerdict(
        ticker="EDX3",
        code="neutral",
        metrics_delta=0,
        publications_written_delta=0,
        qa_issues_delta=0,
        notes=(),
    )
    out = aggregate_verdict({"EDX1": a, "EDX2": b, "EDX3": c})
    assert out == "neutral"


def test_memory_has_new_entry_robust_to_dash_variants() -> None:
    """The MEMORY.md template uses an em-dash (—). Some agents (or
    keyboards) might emit a hyphen-minus instead. The strict regex
    rejects those as a guard against garbage entries."""
    bad_entry = "### evolve(5) - 2026-05-03 - period_unparseable\n"
    assert has_new_entry_since("", bad_entry, 5) is False


def test_memory_count_is_stable_under_unicode_and_rejects_invalid_classes(
    tmp_path: Path,
) -> None:
    """The PATCH_HEADER_RE only accepts [A-Za-z0-9_]+ failure_class.
    Non-ASCII class names are rejected (logs are noisy enough; we want
    a stable index). Anti-pattern bullets pass through unicode bodies."""
    p = tmp_path / "m.md"
    p.write_text(
        "## Patches log\n"
        "\n"
        "### evolve(10) — 2026-05-03 — period_unparseable\n"
        "- Tick: #10\n"
        "\n"
        "### evolve(11) — 2026-05-03 — кириллица_only\n"
        "- Tick: #11\n"
        "\n"
        "## Anti-patterns\n"
        "- Юникод тоже считается за пункт.\n"
        "- ASCII bullet.\n",
        encoding="utf-8",
    )
    digest = read(p)
    assert digest.patch_entries == 1
    assert digest.last_tick == 10
    assert "period_unparseable" in digest.failure_classes
    assert "кириллица_only" not in digest.failure_classes
    assert digest.anti_patterns_count == 2


def test_company_row_synthetic_ticker_no_collision_with_moex() -> None:
    """Pilot anti-regression: synthetic ticker MUST start with "EDX"
    so it never collides with a 3-5 letter MOEX symbol."""
    rows = [
        CompanyRow(company_id=str(cid), name=f"Co {cid}", type="non_bank")
        for cid in (1, 17, 1210, 38588, 999999)
    ]
    for row in rows:
        assert row.synthetic_ticker.startswith("EDX")
        # Real MOEX tickers are 3-5 uppercase letters with no digits at
        # the start; ours are >5 chars or contain digits.
        assert len(row.synthetic_ticker) > 5 or any(
            ch.isdigit() for ch in row.synthetic_ticker
        )


def test_aggregate_verdict_does_not_silently_succeed_on_empty_batch() -> None:
    """A picker bug returning empty verdicts dict must NOT be treated as ok."""
    assert aggregate_verdict({}) == "fail"


def test_classify_returns_unknown_when_all_evidence_for_other_tickers(
    tmp_path: Path,
) -> None:
    """Pilot Phase 1 catch: cross-ticker leakage anti-regression."""
    log = tmp_path / "p.log"
    log.write_text(
        json.dumps(
            {
                "event": "metric_extract_failed",
                "level": "error",
                "ticker": "EDX_other",
                "error": "UNIQUE constraint failed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = classify_failures(
        log, state_slice={"EDX_target": {}}, failing_tickers=["EDX_target"]
    )
    assert out[0].code == "unknown"


def test_pilot_report_template_present() -> None:
    """Patch 45 ships a template the operator fills in."""
    p = Path(__file__).resolve().parents[2] / "deploy" / "PILOT_REPORT_template.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    for anchor in ("Phase 1", "Phase 2", "Phase 3", "Sign-off"):
        assert anchor in text


def test_memory_file_contains_anti_pattern_for_taxonomy_smear() -> None:
    """We documented the per-ticker filter rule in MEMORY.md so any
    future tick that touches taxonomy.py sees it in STEP 0."""
    p = Path(__file__).resolve().parents[2] / "evolution" / "MEMORY.md"
    text = p.read_text(encoding="utf-8")
    assert "ticker_logs" in text
    assert "smear" in text.lower()
