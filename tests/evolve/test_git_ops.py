"""git_ops: tick branch lifecycle, whitelist gate, FF merge (Patch 43)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from edx.evolve import git_ops

# --------------------------------------------------------------- helpers

def _run(cwd: Path, argv: list[str]) -> str:
    return subprocess.check_output(
        ["git", "-C", str(cwd), *argv], text=True, stderr=subprocess.STDOUT
    )


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, ["init", "-q", "-b", "master"])
    _run(path, ["config", "user.email", "evolve@test"])
    _run(path, ["config", "user.name", "Evolve Test"])
    (path / "src" / "edx").mkdir(parents=True, exist_ok=True)
    (path / "src" / "edx" / "core.py").write_text(
        "def hello(): return 'hi'\n", encoding="utf-8"
    )
    (path / "tests").mkdir(parents=True, exist_ok=True)
    (path / "tests" / "test_core.py").write_text("def test_x(): pass\n")
    (path / "evolution").mkdir(parents=True, exist_ok=True)
    (path / "evolution" / "MEMORY.md").write_text(
        "# Self-Evolve Long-Term Memory\n", encoding="utf-8"
    )
    (path / "deploy").mkdir(parents=True, exist_ok=True)
    (path / "deploy" / "secret.sh").write_text("# secret\n")
    (path / ".env.example").write_text("SECRET=\n")
    _run(path, ["add", "."])
    _run(path, ["commit", "-q", "-m", "init"])
    return path


def _make_origin(repo: Path, origin_path: Path) -> Path:
    """Create a bare clone to act as origin for push tests."""
    origin_path.mkdir(parents=True, exist_ok=True)
    _run(origin_path, ["init", "--bare", "-q", "-b", "master"])
    _run(repo, ["remote", "add", "origin", str(origin_path)])
    _run(repo, ["push", "-q", "origin", "master"])
    return origin_path


# --------------------------------------------------------------- tests

def test_tick_branch_name_format() -> None:
    assert git_ops.tick_branch_name(7) == "evolve/tick-7"


def test_tick_branch_name_rejects_zero() -> None:
    with pytest.raises(ValueError):
        git_ops.tick_branch_name(0)


def test_create_tick_branch_basic(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    branch = git_ops.create_tick_branch(repo, 5)
    assert branch == "evolve/tick-5"
    assert git_ops.current_branch(repo) == "evolve/tick-5"


def test_whitelist_blocks_env(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    git_ops.create_tick_branch(repo, 1)
    (repo / ".env").write_text("LEAK=1\n", encoding="utf-8")
    violations = git_ops.whitelist_violations(repo)
    assert ".env" in violations


def test_whitelist_blocks_deploy(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    git_ops.create_tick_branch(repo, 1)
    (repo / "deploy" / "secret.sh").write_text("MODIFIED\n", encoding="utf-8")
    violations = git_ops.whitelist_violations(repo)
    assert any(v.startswith("deploy/") for v in violations)


def test_whitelist_allows_src_and_memory(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    git_ops.create_tick_branch(repo, 1)
    (repo / "src" / "edx" / "new_file.py").write_text("ok\n", encoding="utf-8")
    (repo / "evolution" / "MEMORY.md").write_text(
        "# Self-Evolve Long-Term Memory\n# new entry\n", encoding="utf-8"
    )
    assert git_ops.whitelist_violations(repo) == []


def test_stage_changes_skips_violations(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    git_ops.create_tick_branch(repo, 1)
    (repo / "src" / "edx" / "ok.py").write_text("good\n", encoding="utf-8")
    (repo / ".env").write_text("BAD=1\n", encoding="utf-8")
    staged = git_ops.stage_changes(repo)
    # The whitelist gate above should have stopped us before stage_changes;
    # but stage_changes itself MUST also refuse violations.
    assert "src/edx/ok.py" in staged
    assert all(".env" not in p for p in staged)


def test_commit_and_merge_no_changes(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    _make_origin(repo, tmp_path / "o.git")
    git_ops.create_tick_branch(repo, 2)
    res = git_ops.commit_and_merge(
        repo, 2, "evolve(2): empty", push=False
    )
    assert res.notes == ("no_changes",)
    assert res.pushed is False


def test_commit_and_merge_blocks_violations(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    _make_origin(repo, tmp_path / "o.git")
    git_ops.create_tick_branch(repo, 3)
    (repo / "src" / "edx" / "ok.py").write_text("ok\n", encoding="utf-8")
    (repo / "deploy" / "evil.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    res = git_ops.commit_and_merge(
        repo, 3, "evolve(3): bad", push=False
    )
    assert any(n.startswith("whitelist_violations:") for n in res.notes)
    assert res.pushed is False


def test_commit_and_merge_happy_path(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    _make_origin(repo, tmp_path / "o.git")
    git_ops.create_tick_branch(repo, 4)
    (repo / "src" / "edx" / "new_one.py").write_text("ok\n", encoding="utf-8")
    (repo / "evolution" / "MEMORY.md").write_text(
        "# Self-Evolve Long-Term Memory\n\n### evolve(4) — 2026-05-03 — foo\n",
        encoding="utf-8",
    )

    res = git_ops.commit_and_merge(
        repo, 4, "evolve(4): patch", push=True
    )
    assert res.commit_sha
    assert res.pushed is True
    assert res.rolled_back is False
    # Tick branch is gone.
    branches = _run(repo, ["branch"])
    assert "evolve/tick-4" not in branches


def test_commit_and_merge_rejects_non_protected_target(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    _make_origin(repo, tmp_path / "o.git")
    git_ops.create_tick_branch(repo, 5)
    (repo / "src" / "edx" / "x.py").write_text("ok\n", encoding="utf-8")
    res = git_ops.commit_and_merge(
        repo, 5, "evolve(5): random", push=False, target_branch="dev"
    )
    assert any(n.startswith("target_not_protected:") for n in res.notes)


def test_commit_and_merge_not_on_tick_branch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    _make_origin(repo, tmp_path / "o.git")
    # We never branched.
    res = git_ops.commit_and_merge(
        repo, 6, "evolve(6): no", push=False
    )
    assert any(n.startswith("not_on_tick_branch:") for n in res.notes)


def test_commit_and_merge_recovers_when_head_drifted_to_master(
    tmp_path: Path,
) -> None:
    """Anti-regression for production tick #77: HEAD got switched back to
    master mid-tick (concurrent operator activity). The agent's working-tree
    edits + the tick branch both still exist. ``commit_and_merge`` must
    soft-recover by symbolic-ref'ing HEAD back onto evolve/tick-N rather
    than aborting the entire tick as ``not_on_tick_branch``."""
    repo = _make_repo(tmp_path / "r")
    _make_origin(repo, tmp_path / "o.git")
    git_ops.create_tick_branch(repo, 77)
    # Stage agent's "edit" while on the tick branch.
    (repo / "src" / "edx" / "patch.py").write_text(
        "ok\n", encoding="utf-8"
    )
    (repo / "evolution" / "MEMORY.md").write_text(
        "# Self-Evolve Long-Term Memory\n\n### evolve(77) — 2026-05-04 — foo\n",
        encoding="utf-8",
    )
    # Simulate the bug: HEAD gets flipped to master (e.g. operator did a
    # git operation on master in the same working tree). The tick branch
    # still exists at the same sha; the working-tree edits are still there.
    _run(repo, ["symbolic-ref", "HEAD", "refs/heads/master"])

    res = git_ops.commit_and_merge(
        repo, 77, "evolve(77): patch", push=True
    )
    assert res.commit_sha, f"expected successful commit, got {res.notes}"
    assert res.pushed is True
    assert any(n.startswith("head_recovered_from:") for n in res.notes)


def test_abandon_branch_idempotent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    git_ops.create_tick_branch(repo, 7)
    git_ops.abandon_branch(repo, 7)
    assert git_ops.current_branch(repo) == "master"
    # Second call doesn't raise.
    git_ops.abandon_branch(repo, 7)


def test_abandon_branch_master_untouched(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "r")
    initial_sha = _run(repo, ["rev-parse", "master"]).strip()
    git_ops.create_tick_branch(repo, 8)
    (repo / "src" / "edx" / "tmp.py").write_text("ok\n", encoding="utf-8")
    _run(repo, ["add", "src/edx/tmp.py"])
    _run(repo, ["commit", "-q", "-m", "tick stuff"])
    git_ops.abandon_branch(repo, 8)
    after_sha = _run(repo, ["rev-parse", "master"]).strip()
    assert initial_sha == after_sha
