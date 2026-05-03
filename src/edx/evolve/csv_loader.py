"""Load ``e-disclosure-companies.csv`` into typed rows (Patch 39).

The CSV is operator-curated: three columns ``id``, ``name``, ``type``.
``type`` is the issuer profile (``bank`` or ``non_bank``) that drives the
Metric Extractor's prompt selection downstream — no heuristics are
applied here.

The synthetic ticker ``EDX{id}`` lets the existing ``edx`` pipeline (which
expects MOEX-style 3-5 letter tickers) co-exist with these issuers
without any DB migration. Real MOEX tickers from ``config/tickers.yaml``
keep their natural symbols.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

CompanyType = Literal["bank", "non_bank"]

REQUIRED_COLUMNS: Final[tuple[str, ...]] = ("id", "name", "type")
ALLOWED_TYPES: Final[frozenset[str]] = frozenset({"bank", "non_bank"})


@dataclass(frozen=True, slots=True)
class CompanyRow:
    """One issuer from ``e-disclosure-companies.csv``.

    ``company_id`` is kept as ``str`` because e-disclosure IDs are
    treated as TEXT throughout the schema (``tickers.e_disclosure_id``).
    """

    company_id: str
    name: str
    type: CompanyType

    @property
    def synthetic_ticker(self) -> str:
        return f"EDX{self.company_id}"


def load_companies(
    path: Path = Path("e-disclosure-companies.csv"),
) -> list[CompanyRow]:
    """Read and validate the companies CSV.

    Raises ``FileNotFoundError`` if ``path`` is missing, ``ValueError``
    on any of: missing header, missing required column, empty ``id`` or
    ``name``, ``type`` outside ``{"bank","non_bank"}``.
    Type strings are normalised to lowercase.
    """
    if not path.exists():
        raise FileNotFoundError(f"companies CSV not found: {path}")

    rows: list[CompanyRow] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(
                f"{path}: header missing — expected columns {REQUIRED_COLUMNS}"
            )
        missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"{path}: missing column(s) {missing}; "
                f"got {list(reader.fieldnames)}"
            )

        for line_no, raw in enumerate(reader, start=2):
            company_id = (raw.get("id") or "").strip()
            name = (raw.get("name") or "").strip()
            type_raw = (raw.get("type") or "").strip().lower()
            if not company_id:
                raise ValueError(f"{path}:{line_no}: empty id")
            if not name:
                raise ValueError(f"{path}:{line_no}: empty name")
            if type_raw not in ALLOWED_TYPES:
                raise ValueError(
                    f"{path}:{line_no}: type must be one of "
                    f"{sorted(ALLOWED_TYPES)}, got {type_raw!r}"
                )
            rows.append(
                CompanyRow(
                    company_id=company_id,
                    name=name,
                    type=type_raw,  # type: ignore[arg-type]  # narrowed by check above
                )
            )
    return rows
