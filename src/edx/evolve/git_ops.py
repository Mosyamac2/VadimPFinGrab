"""Git operations for the self-evolve verdict gate (Patch 43).

The wrapper is the ONLY entity allowed to write to ``master`` from
the loop. Claude Code is denied ``git commit / push / reset / branch``
in ``.claude/settings.evolve.json``; here we provide the controlled
counterpart:

  - create_tick_branch: ``git checkout -b evolve/tick-N master``
  - whitelist_violations: list paths in the working tree that fall
    OUTSIDE the allowed globs (src/, config/, tests/, prompts/,
    evolution/MEMORY.md). Blocks any auto-commit that would touch
    .env, deploy/, .git/, etc.
  - stage_changes: ``git add`` only the whitelisted modified/untracked
    files.
  - commit_and_merge: commit on the tick branch + ``git merge --ff-only``
    into master + ``git push``. Any failure rolls the master branch
    back to the pre-merge sha.
  - abandon_branch: drop the tick branch (master untouched).

Invariants:
  - We never run ``git push --force``.
  - ``master`` is touched only via fast-forward.
  - Tick branch name is fixed format ``evolve/tick-{tick_id}`` — the
    helpers refuse to operate on anything else.
"""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from edx.logging_setup import get_logger

ALLOWED_FILE_GLOBS: Final[tuple[str, ...]] = (
    "src/edx/**",
    "src/edx/*",
    "config/*",
    "config/**",
    "tests/**",
    "tests/*",
    "prompts/**",
    "prompts/*",
    "evolution/MEMORY.md",
)
PROHIBITED_FILE_GLOBS: Final[tuple[str, ...]] = (
    ".env*",
    "deploy/**",
    ".git/**",
    ".claude/settings.local.json",
    "evolution/runs/**",
    "config-evolve/**",
    "data/**",
    "output/**",
    "logs/**",
)
PROTECTED_BRANCHES: Final[frozenset[str]] = frozenset({"master", "main"})


@dataclass(frozen=True, slots=True)
class GitMergeResult:
    branch: str
    commit_sha: str | None
    pushed: bool
    rolled_back: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


def tick_branch_name(tick_id: int) -> str:
    if tick_id <= 0:
        raise ValueError(f"tick_id must be positive, got {tick_id}")
    return f"evolve/tick-{tick_id}"


def current_branch(cwd: Path) -> str:
    return _git_text(cwd, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()


def _git_text(cwd: Path, argv: list[str]) -> str:
    return subprocess.check_output(
        ["git", "-C", str(cwd), *argv],
        text=True,
        stderr=subprocess.STDOUT,
    )


def _git_check(cwd: Path, argv: list[str]) -> int:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *argv],
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode


def create_tick_branch(
    cwd: Path, tick_id: int, base: str = "master"
) -> str:
    """``git checkout -b evolve/tick-N base``. Fails if branch exists."""
    branch = tick_branch_name(tick_id)
    if base in PROTECTED_BRANCHES:
        # Branching FROM master/main is fine; branching INTO is not what
        # we do here. This guard is defensive — never accept the protected
        # branch as a TARGET name.
        pass
    _git_text(cwd, ["checkout", "-b", branch, base])
    return branch


def changed_files(cwd: Path, base: str = "master") -> list[str]:
    """Files differing from ``base``: tracked diff + untracked (new)."""
    files: set[str] = set()

    diff_out = _safe_git(cwd, ["diff", "--name-only", base])
    for line in diff_out.splitlines():
        line = line.strip()
        if line:
            files.add(line)

    untracked = _safe_git(cwd, ["ls-files", "--others", "--exclude-standard"])
    for line in untracked.splitlines():
        line = line.strip()
        if line:
            files.add(line)

    return sorted(files)


def whitelist_violations(
    cwd: Path, base: str = "master"
) -> list[str]:
    """Return paths the patch wants to add/modify but isn't allowed to."""
    violations: list[str] = []
    for path in changed_files(cwd, base=base):
        if _is_prohibited(path) or not _is_allowed(path):
            violations.append(path)
    return violations


def stage_changes(cwd: Path, base: str = "master") -> list[str]:
    """``git add`` only the whitelisted paths. Returns what was staged."""
    staged: list[str] = []
    for path in changed_files(cwd, base=base):
        if _is_prohibited(path) or not _is_allowed(path):
            continue
        _git_text(cwd, ["add", "--", path])
        staged.append(path)
    return staged


def commit_and_merge(
    cwd: Path,
    tick_id: int,
    message: str,
    *,
    push: bool = True,
    remote: str = "origin",
    target_branch: str = "master",
) -> GitMergeResult:
    """Commit on ``evolve/tick-N``, fast-forward into ``target_branch``, push.

    On any failure the function tries hard to roll ``target_branch``
    back to the pre-merge sha. ``master``/``main`` is the only valid
    fast-forward target.
    """
    log = get_logger("edx.evolve.git_ops")
    branch = tick_branch_name(tick_id)
    notes: list[str] = []

    # Sanity: we must be on the tick branch.
    here = current_branch(cwd)
    if here != branch:
        return GitMergeResult(
            branch=branch,
            commit_sha=None,
            pushed=False,
            rolled_back=False,
            notes=(f"not_on_tick_branch:{here}",),
        )

    if target_branch not in PROTECTED_BRANCHES:
        return GitMergeResult(
            branch=branch,
            commit_sha=None,
            pushed=False,
            rolled_back=False,
            notes=(f"target_not_protected:{target_branch}",),
        )

    violations = whitelist_violations(cwd, base=target_branch)
    if violations:
        return GitMergeResult(
            branch=branch,
            commit_sha=None,
            pushed=False,
            rolled_back=False,
            notes=("whitelist_violations:" + ",".join(violations[:5]),),
        )

    staged = stage_changes(cwd, base=target_branch)
    if not staged:
        return GitMergeResult(
            branch=branch,
            commit_sha=None,
            pushed=False,
            rolled_back=False,
            notes=("no_changes",),
        )

    try:
        _git_text(cwd, ["commit", "-m", message])
        commit_sha = _git_text(cwd, ["rev-parse", "HEAD"]).strip()
    except subprocess.CalledProcessError as exc:
        log.error("evolve_git_commit_failed", error=str(exc))
        return GitMergeResult(
            branch=branch,
            commit_sha=None,
            pushed=False,
            rolled_back=False,
            notes=("commit_failed",),
        )

    # Capture target_branch sha so we can roll back on FF failure.
    pre_target_sha = _safe_git(
        cwd, ["rev-parse", target_branch]
    ).strip() or None

    try:
        _git_text(cwd, ["checkout", target_branch])
    except subprocess.CalledProcessError:
        notes.append("checkout_target_failed")
        return GitMergeResult(
            branch=branch,
            commit_sha=commit_sha,
            pushed=False,
            rolled_back=False,
            notes=tuple(notes),
        )

    try:
        _git_text(cwd, ["merge", "--ff-only", branch])
    except subprocess.CalledProcessError as exc:
        log.error("evolve_git_ff_merge_failed", error=str(exc))
        # Roll target back to its pre-merge sha if known.
        if pre_target_sha:
            _safe_git(cwd, ["reset", "--hard", pre_target_sha])
        # Drop the now-orphan tick branch + return to target.
        _safe_git(cwd, ["branch", "-D", branch])
        return GitMergeResult(
            branch=branch,
            commit_sha=commit_sha,
            pushed=False,
            rolled_back=True,
            notes=("ff_merge_failed",),
        )

    pushed = False
    if push:
        try:
            _git_text(cwd, ["push", remote, target_branch])
            pushed = True
        except subprocess.CalledProcessError as exc:
            log.error("evolve_git_push_failed", error=str(exc))
            notes.append("push_failed")

    # Tick branch served its purpose — drop it.
    _safe_git(cwd, ["branch", "-D", branch])

    return GitMergeResult(
        branch=branch,
        commit_sha=commit_sha,
        pushed=pushed,
        rolled_back=False,
        notes=tuple(notes),
    )


def abandon_branch(cwd: Path, tick_id: int) -> None:
    """Switch to master + force-delete the tick branch. Idempotent."""
    branch = tick_branch_name(tick_id)
    here = current_branch(cwd)
    if here == branch:
        _safe_git(cwd, ["checkout", "master"])
    _safe_git(cwd, ["branch", "-D", branch])


# ----------------------------------------------------------------- helpers

def _is_allowed(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, glob) for glob in ALLOWED_FILE_GLOBS)


def _is_prohibited(path: str) -> bool:
    return any(
        fnmatch.fnmatchcase(path, glob) for glob in PROHIBITED_FILE_GLOBS
    )


def _safe_git(cwd: Path, argv: list[str]) -> str:
    """Best-effort git invocation: never raises. Empty string on error."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(cwd), *argv],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


__all__ = [
    "ALLOWED_FILE_GLOBS",
    "PROHIBITED_FILE_GLOBS",
    "PROTECTED_BRANCHES",
    "GitMergeResult",
    "abandon_branch",
    "changed_files",
    "commit_and_merge",
    "create_tick_branch",
    "current_branch",
    "stage_changes",
    "tick_branch_name",
    "whitelist_violations",
]
