"""MetricsRepo: replace_for_publication atomicity."""

from __future__ import annotations

import sqlite3

import pytest

from edx.config import TickerEntry
from edx.storage import (
    Database,
    DocumentInput,
    DocumentsRepo,
    MetricInput,
    MetricsRepo,
    PublicationsRepo,
    TickersRepo,
)


@pytest.fixture
def seeded(
    tmp_db: Database, conn: sqlite3.Connection
) -> tuple[Database, sqlite3.Connection, str, int]:
    TickersRepo(tmp_db, conn).upsert_from_config(
        [TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")]
    )
    PublicationsRepo(tmp_db, conn).upsert_discovered(
        publication_id="pub-1", ticker="SBER", publication_type="report",
        publication_date="2026-03-01", source_url="https://x",
    )
    docs_repo = DocumentsRepo(tmp_db, conn)
    docs_repo.add_documents(
        "pub-1", [DocumentInput(relative_path="report.pdf", file_hash="h1")]
    )
    doc_id = docs_repo.list_for_publication("pub-1")[0].document_id
    return tmp_db, conn, "pub-1", doc_id


def _input(name: str, *, doc_id: int, period: str = "FY") -> MetricInput:
    return MetricInput(
        ticker="SBER",
        reporting_date="2025-12-31",
        period_type=period,  # type: ignore[arg-type]
        reporting_standard="IFRS",
        metric_name=name,
        value=100.0,
        currency="RUB",
        unit="ones",
        source_document_id=doc_id,
    )


def test_replace_writes_new_rows(
    seeded: tuple[Database, sqlite3.Connection, str, int],
) -> None:
    db, conn, pub_id, doc_id = seeded
    repo = MetricsRepo(db, conn)
    inserted = repo.replace_for_publication(
        pub_id,
        [_input("revenue", doc_id=doc_id), _input("net_income", doc_id=doc_id)],
    )
    assert inserted == 2
    rows = repo.list_for_publication(pub_id)
    assert {r.metric_name for r in rows} == {"revenue", "net_income"}


def test_replace_overwrites_existing(
    seeded: tuple[Database, sqlite3.Connection, str, int],
) -> None:
    db, conn, pub_id, doc_id = seeded
    repo = MetricsRepo(db, conn)
    repo.replace_for_publication(pub_id, [_input("revenue", doc_id=doc_id)])
    repo.replace_for_publication(
        pub_id,
        [
            _input("ebitda", doc_id=doc_id),
            _input("total_assets", doc_id=doc_id),
        ],
    )
    rows = repo.list_for_publication(pub_id)
    assert {r.metric_name for r in rows} == {"ebitda", "total_assets"}


def test_replace_atomic_rolls_back_on_failure(
    seeded: tuple[Database, sqlite3.Connection, str, int],
) -> None:
    db, conn, pub_id, doc_id = seeded
    repo = MetricsRepo(db, conn)
    repo.replace_for_publication(
        pub_id,
        [
            _input("revenue", doc_id=doc_id),
            _input("ebitda", doc_id=doc_id),
            _input("net_income", doc_id=doc_id),
        ],
    )
    bad = MetricInput(
        ticker="SBER",
        reporting_date="2025-12-31",
        period_type="bad-period",  # type: ignore[arg-type]
        reporting_standard="IFRS",
        metric_name="revenue",
        value=999.0,
        currency="RUB",
        unit="ones",
        source_document_id=doc_id,
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.replace_for_publication(pub_id, [_input("ebitda", doc_id=doc_id), bad])
    rows = repo.list_for_publication(pub_id)
    assert {r.metric_name for r in rows} == {"revenue", "ebitda", "net_income"}
    for row in rows:
        assert row.value == 100.0  # original values preserved
