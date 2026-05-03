"""README.md must mention the self-evolve install workflow."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"


def test_section_11_self_evolution_present() -> None:
    text = README.read_text(encoding="utf-8")
    assert "## 11. Self-Evolution loop" in text


def test_readme_mentions_install_script() -> None:
    text = README.read_text(encoding="utf-8")
    assert "deploy/install_claude_code.sh" in text


def test_readme_mentions_memory_and_canary() -> None:
    text = README.read_text(encoding="utf-8")
    assert "evolution/MEMORY.md" in text
    assert "edx evolve canary capture" in text


def test_readme_mentions_agent_enabled_var() -> None:
    text = README.read_text(encoding="utf-8")
    assert "EDX_EVOLVE_AGENT_ENABLED" in text


def test_readme_section_12_perspectives_renumbered() -> None:
    text = README.read_text(encoding="utf-8")
    assert "## 12. Перспективы" in text
