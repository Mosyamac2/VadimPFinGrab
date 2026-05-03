"""Production rollout artefacts: PRODUCTION_ENABLE.md, cron, runbook (Patch 46)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRODUCTION_ENABLE = REPO / "deploy" / "PRODUCTION_ENABLE.md"
EVOLVE_SUMMARY_CRON = REPO / "deploy" / "cron" / "edx-evolve-summary.crontab"
USER_GUIDE = REPO / "USER_GUIDE.md"
MAKEFILE = REPO / "Makefile"


def test_production_enable_template_present() -> None:
    assert PRODUCTION_ENABLE.exists()


def test_production_enable_has_pre_flight_and_signoff() -> None:
    text = PRODUCTION_ENABLE.read_text(encoding="utf-8")
    for anchor in (
        "Pre-flight",
        "Cutover",
        "Sign-off",
        "Откат",
        "EDX_EVOLVE_AGENT_ENABLED",
    ):
        assert anchor in text


def test_evolve_summary_cron_present() -> None:
    assert EVOLVE_SUMMARY_CRON.exists()
    text = EVOLVE_SUMMARY_CRON.read_text(encoding="utf-8")
    assert "edx evolve report" in text


def test_user_guide_runbook_sections_present() -> None:
    """The runbook must cover the four canonical incidents."""
    text = USER_GUIDE.read_text(encoding="utf-8")
    for anchor in (
        "Self-Evolve runbook",
        "Incident: ежедневный budget",
        "Incident: master сломан",
        "Incident: skiplist >",
        "Incident: канареечная регрессия",
    ):
        assert anchor in text, f"missing anchor: {anchor}"


def test_makefile_has_slo_smoke_target() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "slo-smoke:" in text
