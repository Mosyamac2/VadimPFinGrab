"""Schema sanity for `.claude/settings.evolve.json` and slash command (Patch 42)."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.evolve.json"
SLASH_PATH = REPO_ROOT / ".claude" / "commands" / "edx-evolve-fix.md"


REQUIRED_DENY_RULES = (
    "Edit(.env*)",
    "Edit(deploy/**)",
    "Bash(git push *)",
    "Bash(git reset --hard *)",
    "Bash(git commit *)",
    "Bash(rm -rf *)",
    "WebFetch",
    "WebSearch",
)


def test_settings_evolve_file_present() -> None:
    assert SETTINGS_PATH.exists(), f"missing {SETTINGS_PATH}"


def test_settings_evolve_is_valid_json() -> None:
    json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def test_settings_evolve_has_required_deny_rules() -> None:
    payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    deny = payload["permissions"]["deny"]
    for rule in REQUIRED_DENY_RULES:
        assert rule in deny, f"missing deny rule: {rule}"


def test_settings_evolve_allow_and_deny_disjoint() -> None:
    """Sanity: nothing is both allowed and denied (denies don't no-op)."""
    payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    allow = set(payload["permissions"]["allow"])
    deny = set(payload["permissions"]["deny"])
    assert allow.isdisjoint(deny)


def test_slash_command_present() -> None:
    assert SLASH_PATH.exists(), f"missing {SLASH_PATH}"


def test_slash_command_mentions_step_anchors() -> None:
    text = SLASH_PATH.read_text(encoding="utf-8")
    for anchor in (
        "STEP 0",
        "evolution/MEMORY.md",
        "STEP 4",
        "STEP 5",
        "DO NOT commit",
        "DO NOT push",
    ):
        assert anchor in text, f"slash command missing anchor: {anchor}"


def test_slash_command_has_argument_hint() -> None:
    text = SLASH_PATH.read_text(encoding="utf-8")
    assert "argument-hint:" in text
