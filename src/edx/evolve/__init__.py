"""Self-evolution loop modules.

The package is opt-in — the regular ``edx update`` pipeline does not
import anything from here. The ``edx evolve`` CLI subcommand (added in
Patch 40) instantiates these modules to schedule batches of 3 companies
from ``e-disclosure-companies.csv`` and (in Patch 42+) drive Claude Code
in headless mode.
"""

from edx.evolve.canaries import (
    CANARY_TICKERS,
    CanaryReport,
    canary_baseline_path,
    check_canaries,
    take_canary_baseline,
)
from edx.evolve.claude_runner import ClaudeRunResult, run_agent
from edx.evolve.csv_loader import CompanyRow, CompanyType, load_companies
from edx.evolve.git_ops import GitMergeResult
from edx.evolve.git_ops import abandon_branch as git_abandon_branch
from edx.evolve.git_ops import commit_and_merge as git_commit_and_merge
from edx.evolve.git_ops import create_tick_branch as git_create_tick_branch
from edx.evolve.memory import (
    MEMORY_PATH,
    MemoryDigest,
    diff_summary,
    has_new_entry_since,
)
from edx.evolve.memory import read as read_memory
from edx.evolve.picker import PickerInput, pick_next_batch
from edx.evolve.runner import PipelineRunResult, run_pipeline_on_batch
from edx.evolve.snapshot import TickerSnapshot, snapshot_batch, snapshot_ticker
from edx.evolve.synth import write_evolve_config
from edx.evolve.taxonomy import TaxonomyCode, TaxonomyEntry, classify_failures
from edx.evolve.tick import read_moex_e_disclosure_ids, run_one_tick
from edx.evolve.verdict import (
    TickerVerdict,
    VerdictCode,
    aggregate_verdict,
    compute_verdict,
)

__all__ = [
    "CANARY_TICKERS",
    "CanaryReport",
    "ClaudeRunResult",
    "CompanyRow",
    "CompanyType",
    "GitMergeResult",
    "MEMORY_PATH",
    "MemoryDigest",
    "PickerInput",
    "PipelineRunResult",
    "TaxonomyCode",
    "TaxonomyEntry",
    "TickerSnapshot",
    "TickerVerdict",
    "VerdictCode",
    "aggregate_verdict",
    "canary_baseline_path",
    "check_canaries",
    "classify_failures",
    "compute_verdict",
    "diff_summary",
    "git_abandon_branch",
    "git_commit_and_merge",
    "git_create_tick_branch",
    "has_new_entry_since",
    "load_companies",
    "pick_next_batch",
    "read_memory",
    "read_moex_e_disclosure_ids",
    "run_agent",
    "run_one_tick",
    "run_pipeline_on_batch",
    "snapshot_batch",
    "snapshot_ticker",
    "take_canary_baseline",
    "write_evolve_config",
]
