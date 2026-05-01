"""ValidatorService — exercising rules over synthetic publications."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest

from edx.config import TickerEntry
from edx.stages.validator.service import ValidatorService
from edx.storage import (
    Database,
    DocumentInput,
    DocumentsRepo,
    MetricInput,
    MetricsRepo,
    PublicationsRepo,
    QAIssuesRepo,
    TickersRepo,
)


def _seed_publication(
    db: Database,
    pub_id: str,
    *,
    metric_specs: list[tuple[str, float | None, str, str]],
    reporting_date: str = "2025-12-31",
    period_type: str = "FY",
    reporting_standard: str = "IFRS",
) -> int:
    """Insert one publication with its metrics. Returns source_document_id."""
    with closing(db.connect()) as conn:
        pubs = PublicationsRepo(db, conn)
        docs = DocumentsRepo(db, conn)
        metrics_repo = MetricsRepo(db, conn)

        pubs.upsert_discovered(
            publication_id=pub_id,
            ticker="SBER",
            publication_type="report",
            publication_date=reporting_date,
            source_url=f"https://example.test/{pub_id}",
        )
        for status in ("downloaded", "unpacked", "classified", "extracted"):
            pubs.mark_status(pub_id, status)  # type: ignore[arg-type]

        docs.add_documents(
            pub_id,
            [DocumentInput(relative_path="report.pdf", file_hash=f"h-{pub_id}")],
        )
        doc_id = docs.list_for_publication(pub_id)[0].document_id

        rows = [
            MetricInput(
                ticker="SBER",
                reporting_date=reporting_date,
                period_type=period_type,  # type: ignore[arg-type]
                reporting_standard=reporting_standard,  # type: ignore[arg-type]
                metric_name=name,
                value=value,
                currency=currency,
                unit=unit,
                source_document_id=doc_id,
            )
            for name, value, currency, unit in metric_specs
        ]
        metrics_repo.replace_for_publication(pub_id, rows)
    return doc_id


@pytest.fixture
def workspace(tmp_path: Path) -> Database:
    db = Database(tmp_path / "state.sqlite")
    db.migrate()
    with closing(db.connect()) as conn:
        TickersRepo(db, conn).upsert_from_config(
            [TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")]
        )
    return db


def _build_service(db: Database) -> tuple[ValidatorService, object]:
    conn = db.connect()
    service = ValidatorService(
        publications_repo=PublicationsRepo(db, conn),
        metrics_repo=MetricsRepo(db, conn),
        qa_issues_repo=QAIssuesRepo(db, conn),
        completeness_threshold=0.5,
        metrics_per_period=5,
    )
    return service, conn


def test_clean_publication_writes_no_warnings(workspace: Database) -> None:
    db = workspace
    _seed_publication(
        db,
        "pub-clean",
        metric_specs=[
            ("revenue", 1000.0, "RUB", "ones"),
            ("ebitda", 200.0, "RUB", "ones"),
            ("net_income", 100.0, "RUB", "ones"),
            ("total_assets", 5000.0, "RUB", "ones"),
            ("total_debt", 2000.0, "RUB", "ones"),
        ],
    )
    service, conn = _build_service(db)
    try:
        pub = PublicationsRepo(db, conn).get_by_id("pub-clean")
        assert pub is not None
        outcomes = service.run([pub])
        issues = QAIssuesRepo(db, conn).list_for_publication("pub-clean")
        rows = MetricsRepo(db, conn).list_for_publication("pub-clean")
        pub_after = PublicationsRepo(db, conn).get_by_id("pub-clean")
    finally:
        conn.close()

    assert outcomes[0].warnings_count == 0
    assert outcomes[0].metric_rows_flagged == 0
    assert outcomes[0].is_incomplete is False
    assert issues == []
    assert all(r.qa_warning is None for r in rows)
    assert pub_after is not None and pub_after.status == "validated"
    assert pub_after.is_incomplete == 0


def test_publication_with_one_warning(workspace: Database) -> None:
    db = workspace
    _seed_publication(
        db,
        "pub-one",
        metric_specs=[
            ("revenue", -50.0, "RUB", "ones"),  # negative_revenue
            ("ebitda", 200.0, "RUB", "ones"),
            ("net_income", 100.0, "RUB", "ones"),
            ("total_assets", 5000.0, "RUB", "ones"),
            ("total_debt", 2000.0, "RUB", "ones"),
        ],
    )
    service, conn = _build_service(db)
    try:
        pub = PublicationsRepo(db, conn).get_by_id("pub-one")
        assert pub is not None
        outcomes = service.run([pub])
        issues = QAIssuesRepo(db, conn).list_for_publication("pub-one")
        rows = MetricsRepo(db, conn).list_for_publication("pub-one")
    finally:
        conn.close()

    assert outcomes[0].warnings_count == 1
    assert {i.code for i in issues} == {"negative_revenue"}
    flagged = [r for r in rows if r.qa_warning]
    assert len(flagged) == 1
    payload = json.loads(flagged[0].qa_warning or "[]")
    assert payload[0]["code"] == "negative_revenue"


def test_publication_with_three_warnings(workspace: Database) -> None:
    db = workspace
    _seed_publication(
        db,
        "pub-three",
        metric_specs=[
            ("revenue", -10.0, "RUB", "ones"),  # negative_revenue
            ("ebitda", 0.0, "RUB", "ones"),
            ("net_income", 0.0, "USD", "ones"),  # currency_mixed
            ("total_assets", 5000.0, "RUB", "thousands"),  # unit_mixed
            ("total_debt", 2000.0, "RUB", "ones"),
        ],
    )
    service, conn = _build_service(db)
    try:
        pub = PublicationsRepo(db, conn).get_by_id("pub-three")
        assert pub is not None
        service.run([pub])
        issues = QAIssuesRepo(db, conn).list_for_publication("pub-three")
    finally:
        conn.close()

    codes = {i.code for i in issues}
    assert {"negative_revenue", "currency_mixed", "unit_mixed"}.issubset(codes)


def test_yoy_warning_against_previous_period(workspace: Database) -> None:
    db = workspace
    _seed_publication(
        db,
        "pub-2024",
        metric_specs=[("revenue", 100.0, "RUB", "ones")],
        reporting_date="2024-12-31",
    )
    _seed_publication(
        db,
        "pub-2025",
        metric_specs=[("revenue", 1100.0, "RUB", "ones")],
        reporting_date="2025-12-31",
    )

    service, conn = _build_service(db)
    try:
        pub = PublicationsRepo(db, conn).get_by_id("pub-2025")
        assert pub is not None
        service.run([pub])
        issues = QAIssuesRepo(db, conn).list_for_publication("pub-2025")
    finally:
        conn.close()

    assert "suspicious_yoy" in {i.code for i in issues}


def test_no_previous_period_skips_yoy(workspace: Database) -> None:
    db = workspace
    _seed_publication(
        db,
        "pub-only",
        metric_specs=[("revenue", 1000.0, "RUB", "ones")],
        reporting_date="2025-12-31",
    )
    service, conn = _build_service(db)
    try:
        pub = PublicationsRepo(db, conn).get_by_id("pub-only")
        assert pub is not None
        outcomes = service.run([pub])
        issues = QAIssuesRepo(db, conn).list_for_publication("pub-only")
    finally:
        conn.close()
    # No YoY issue (no previous period). May still have completeness — only
    # 1 of expected 5 metrics present.
    assert "suspicious_yoy" not in {i.code for i in issues}
    assert outcomes[0].is_incomplete is True


def test_idempotent_rerun_does_not_duplicate_qa_issues(
    workspace: Database,
) -> None:
    db = workspace
    _seed_publication(
        db,
        "pub-1",
        metric_specs=[
            ("revenue", -1.0, "RUB", "ones"),
            ("ebitda", 1.0, "RUB", "ones"),
            ("net_income", 1.0, "RUB", "ones"),
            ("total_assets", 1.0, "RUB", "ones"),
            ("total_debt", 1.0, "RUB", "ones"),
        ],
    )
    service, conn = _build_service(db)
    try:
        pub = PublicationsRepo(db, conn).get_by_id("pub-1")
        assert pub is not None
        service.run([pub])
        # Reset publication to extracted for the second run.
        PublicationsRepo(db, conn).mark_status("pub-1", "extracted")
        pub = PublicationsRepo(db, conn).get_by_id("pub-1")
        assert pub is not None
        service.run([pub])
        issues = QAIssuesRepo(db, conn).list_for_publication("pub-1")
    finally:
        conn.close()
    # Same set of warnings (no duplicates) after a second run.
    codes = [i.code for i in issues]
    assert codes.count("negative_revenue") == 1
