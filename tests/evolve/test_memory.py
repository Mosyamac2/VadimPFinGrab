"""evolve.memory: parse + has_new_entry_since (Patch 42)."""

from __future__ import annotations

from pathlib import Path

from edx.evolve.memory import (
    diff_summary,
    has_new_entry_since,
    read,
)


def test_read_missing_file_is_empty(tmp_path: Path) -> None:
    digest = read(tmp_path / "absent.md")
    assert digest.raw == ""
    assert digest.patch_entries == 0
    assert digest.last_tick is None
    assert digest.failure_classes == frozenset()
    assert digest.anti_patterns_count == 0


def test_read_counts_patch_entries(tmp_path: Path) -> None:
    p = tmp_path / "MEMORY.md"
    p.write_text(
        "# Self-Evolve Long-Term Memory\n"
        "\n"
        "## Patches log\n"
        "\n"
        "### evolve(7) — 2026-05-04 — period_unparseable\n"
        "- Tick: #7\n"
        "\n"
        "### evolve(12) — 2026-05-05 — metric_synonym_missing\n"
        "- Tick: #12\n"
        "\n"
        "## Anti-patterns\n"
        "- DO NOT widen bank vocab without re-validating non-bank.\n"
        "- DO NOT remove re.IGNORECASE in period.py.\n",
        encoding="utf-8",
    )
    digest = read(p)
    assert digest.patch_entries == 2
    assert digest.last_tick == 12
    assert digest.failure_classes == frozenset(
        {"period_unparseable", "metric_synonym_missing"}
    )
    assert digest.anti_patterns_count == 2


def test_has_new_entry_since_true() -> None:
    before = "## Patches log\n\n_no entries yet_\n"
    after = (
        before
        + "\n### evolve(5) — 2026-05-03 — period_unparseable\n"
        "- Tick: #5\n"
    )
    assert has_new_entry_since(before, after, 5) is True


def test_has_new_entry_since_false_for_other_tick() -> None:
    before = "## Patches log\n\n"
    after = (
        before
        + "### evolve(6) — 2026-05-03 — period_unparseable\n"
        "- Tick: #6\n"
    )
    assert has_new_entry_since(before, after, 5) is False


def test_has_new_entry_since_false_when_already_present() -> None:
    before = "### evolve(5) — 2026-05-03 — period_unparseable\n"
    after = before + "additional content\n"
    assert has_new_entry_since(before, after, 5) is False


def test_has_new_entry_since_strict_format() -> None:
    """Header must match the exact regex shape."""
    before = ""
    # Missing trailing failure_class.
    after_invalid = "### evolve(5) — 2026-05-03\n"
    assert has_new_entry_since(before, after_invalid, 5) is False
    # Wrong dash style.
    after_bad_dash = "### evolve(5) - 2026-05-03 - period_unparseable\n"
    assert has_new_entry_since(before, after_bad_dash, 5) is False


def test_diff_summary_no_change() -> None:
    raw = "abc\ndef\n"
    assert diff_summary(raw, raw) == "(no change)"


def test_diff_summary_lines_delta() -> None:
    out = diff_summary("a\nb\n", "a\nb\nc\nd\n")
    assert "Δ +2" in out


def test_anti_patterns_count_handles_no_section(tmp_path: Path) -> None:
    p = tmp_path / "m.md"
    p.write_text("just text, no anti-patterns header\n", encoding="utf-8")
    assert read(p).anti_patterns_count == 0


def test_anti_patterns_count_stops_at_next_h2(tmp_path: Path) -> None:
    p = tmp_path / "m.md"
    p.write_text(
        "## Anti-patterns\n"
        "- one\n"
        "- two\n"
        "\n"
        "## Companies status\n"
        "- not an anti-pattern\n",
        encoding="utf-8",
    )
    assert read(p).anti_patterns_count == 2
