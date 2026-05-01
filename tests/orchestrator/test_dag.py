"""DAG declarative checks."""

from __future__ import annotations

from edx.orchestrator.dag import STAGES, target_publication_types
from edx.storage import PublicationRow


def _pub(pub_id: str, *, publication_type: str = "report") -> PublicationRow:
    return PublicationRow(
        publication_id=pub_id,
        ticker="SBER",
        publication_type=publication_type,  # type: ignore[arg-type]
        publication_date="2025-12-31",
        source_url="https://x",
        file_hash=None,
        status="extracted",
        last_error=None,
        discovered_at="2025-01-01",
        updated_at="2025-01-01",
    )


def test_stage_order_matches_pipeline_spec() -> None:
    names = [s.name for s in STAGES]
    assert names == [
        "discoverer",
        "downloader",
        "unpacker",
        "classifier",
        "text_extract",
        "metric_extract",
        "event_extract",
        "validator",
        "writer",
        "replicator",
    ]


def test_metric_extract_only_runs_for_reports() -> None:
    metric_extract = next(s for s in STAGES if s.name == "metric_extract")
    pubs = [_pub("r-1", publication_type="report"), _pub("e-1", publication_type="event")]
    selected = target_publication_types(metric_extract, pubs)
    assert [p.publication_id for p in selected] == ["r-1"]


def test_event_extract_only_runs_for_events() -> None:
    event_extract = next(s for s in STAGES if s.name == "event_extract")
    pubs = [_pub("r-1", publication_type="report"), _pub("e-1", publication_type="event")]
    selected = target_publication_types(event_extract, pubs)
    assert [p.publication_id for p in selected] == ["e-1"]


def test_validator_only_runs_for_reports() -> None:
    validator = next(s for s in STAGES if s.name == "validator")
    pubs = [_pub("r-1", publication_type="report"), _pub("e-1", publication_type="event")]
    selected = target_publication_types(validator, pubs)
    assert [p.publication_id for p in selected] == ["r-1"]


def test_per_publication_stages_have_from_and_to_status() -> None:
    per_publication = [s for s in STAGES if s.scope == "publication"]
    for stage in per_publication:
        assert stage.from_status is not None, stage.name
        assert stage.to_status is not None, stage.name


def test_status_chain_consistent_for_main_path() -> None:
    """Statuses chain: discoverer→downloader→unpacker→classifier→text_extract."""
    expected_chain = [
        ("discoverer", None, "discovered"),
        ("downloader", "discovered", "downloaded"),
        ("unpacker", "downloaded", "unpacked"),
        ("classifier", "unpacked", "classified"),
        ("text_extract", "classified", "extracted"),
    ]
    by_name = {s.name: s for s in STAGES}
    for name, from_, to_ in expected_chain:
        stage = by_name[name]
        assert stage.from_status == from_, name
        assert stage.to_status == to_, name
