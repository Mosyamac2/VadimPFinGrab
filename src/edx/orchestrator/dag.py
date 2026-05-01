"""Declarative DAG description (ТЗ §7).

This module is *documentation that runs*: tests assert the order, the
``when`` filters, and the from/to-status invariants without exercising the
real services. The runtime executor (:mod:`edx.orchestrator.runner`) does the
actual work — it stays in lockstep with this list.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from edx.storage import PublicationRow
from edx.storage.models import PublicationStatus

StageScope = Literal["batch", "publication"]


@dataclass(frozen=True)
class StageStep:
    name: str
    scope: StageScope
    from_status: PublicationStatus | None = None
    to_status: PublicationStatus | None = None
    when: Callable[[PublicationRow], bool] | None = None


STAGES: Final[tuple[StageStep, ...]] = (
    StageStep("discoverer", scope="batch", to_status="discovered"),
    StageStep(
        "downloader",
        scope="publication",
        from_status="discovered",
        to_status="downloaded",
    ),
    StageStep(
        "unpacker",
        scope="publication",
        from_status="downloaded",
        to_status="unpacked",
    ),
    StageStep(
        "classifier",
        scope="publication",
        from_status="unpacked",
        to_status="classified",
    ),
    StageStep(
        "text_extract",
        scope="publication",
        from_status="classified",
        to_status="extracted",
    ),
    StageStep(
        "metric_extract",
        scope="publication",
        from_status="extracted",
        to_status="extracted",
        when=lambda p: p.publication_type == "report",
    ),
    StageStep(
        "event_extract",
        scope="publication",
        from_status="extracted",
        to_status="validated",
        when=lambda p: p.publication_type == "event",
    ),
    StageStep(
        "validator",
        scope="publication",
        from_status="extracted",
        to_status="validated",
        when=lambda p: p.publication_type == "report",
    ),
    StageStep("writer", scope="batch", to_status="written"),
    StageStep("replicator", scope="batch"),
)


def target_publication_types(
    stage: StageStep,
    candidates: list[PublicationRow],
) -> list[PublicationRow]:
    """Apply ``stage.when`` filter, returning the candidates that should run."""
    if stage.when is None:
        return list(candidates)
    return [pub for pub in candidates if stage.when(pub)]
