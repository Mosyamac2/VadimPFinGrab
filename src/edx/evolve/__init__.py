"""Self-evolution loop modules.

The package is opt-in — the regular ``edx update`` pipeline does not
import anything from here. The ``edx evolve`` CLI subcommand (added in
Patch 40) instantiates these modules to schedule batches of 3 companies
from ``e-disclosure-companies.csv`` and (in Patch 42+) drive Claude Code
in headless mode.
"""

from edx.evolve.csv_loader import CompanyRow, CompanyType, load_companies
from edx.evolve.picker import PickerInput, pick_next_batch
from edx.evolve.runner import PipelineRunResult, run_pipeline_on_batch
from edx.evolve.snapshot import TickerSnapshot, snapshot_batch, snapshot_ticker
from edx.evolve.synth import write_evolve_config
from edx.evolve.tick import read_moex_e_disclosure_ids, run_one_tick
from edx.evolve.verdict import (
    TickerVerdict,
    VerdictCode,
    aggregate_verdict,
    compute_verdict,
)

__all__ = [
    "CompanyRow",
    "CompanyType",
    "PickerInput",
    "PipelineRunResult",
    "TickerSnapshot",
    "TickerVerdict",
    "VerdictCode",
    "aggregate_verdict",
    "compute_verdict",
    "load_companies",
    "pick_next_batch",
    "read_moex_e_disclosure_ids",
    "run_one_tick",
    "run_pipeline_on_batch",
    "snapshot_batch",
    "snapshot_ticker",
    "write_evolve_config",
]
