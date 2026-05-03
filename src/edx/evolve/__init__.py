"""Self-evolution loop modules.

The package is opt-in — the regular ``edx update`` pipeline does not
import anything from here. The ``edx evolve`` CLI subcommand (added in
Patch 40) instantiates these modules to schedule batches of 3 companies
from ``e-disclosure-companies.csv`` and (in Patch 42+) drive Claude Code
in headless mode.
"""

from edx.evolve.csv_loader import CompanyRow, CompanyType, load_companies
from edx.evolve.picker import PickerInput, pick_next_batch
from edx.evolve.synth import write_evolve_config

__all__ = [
    "CompanyRow",
    "CompanyType",
    "PickerInput",
    "load_companies",
    "pick_next_batch",
    "write_evolve_config",
]
