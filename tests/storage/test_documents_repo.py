"""DocumentsRepo: insert, classify, list, cascade."""

from __future__ import annotations

import sqlite3

import pytest

from edx.config import TickerEntry
from edx.storage import (
    Database,
    DocumentInput,
    DocumentsRepo,
    PublicationsRepo,
    TickersRepo,
)


@pytest.fixture
def with_publication(
    tmp_db: Database, conn: sqlite3.Connection
) -> tuple[Database, sqlite3.Connection, str]:
    TickersRepo(tmp_db, conn).upsert_from_config(
        [TickerEntry(ticker="SBER", e_disclosure_id="1", name="Sberbank")]
    )
    PublicationsRepo(tmp_db, conn).upsert_discovered(
        publication_id="pub-1",
        ticker="SBER",
        publication_type="report",
        publication_date="2026-03-01",
        source_url="https://example.com",
    )
    return tmp_db, conn, "pub-1"


def test_add_documents_writes_rows(
    with_publication: tuple[Database, sqlite3.Connection, str],
) -> None:
    db, conn, pub_id = with_publication
    repo = DocumentsRepo(db, conn)
    repo.add_documents(
        pub_id,
        [
            DocumentInput(relative_path="a.pdf", file_hash="h1", mime_type="application/pdf"),
            DocumentInput(relative_path="b.pdf", file_hash="h2", mime_type="application/pdf"),
        ],
    )
    docs = repo.list_for_publication(pub_id)
    assert {d.relative_path for d in docs} == {"a.pdf", "b.pdf"}


def test_add_documents_unique_path_idempotent(
    with_publication: tuple[Database, sqlite3.Connection, str],
) -> None:
    db, conn, pub_id = with_publication
    repo = DocumentsRepo(db, conn)
    repo.add_documents(pub_id, [DocumentInput(relative_path="a.pdf", file_hash="h1")])
    repo.add_documents(pub_id, [DocumentInput(relative_path="a.pdf", file_hash="h1-new")])
    docs = repo.list_for_publication(pub_id)
    assert len(docs) == 1
    assert docs[0].file_hash == "h1-new"


def test_update_classification_sets_fields(
    with_publication: tuple[Database, sqlite3.Connection, str],
) -> None:
    db, conn, pub_id = with_publication
    repo = DocumentsRepo(db, conn)
    repo.add_documents(pub_id, [DocumentInput(relative_path="a.pdf", file_hash="h1")])
    doc_id = repo.list_for_publication(pub_id)[0].document_id
    repo.update_classification(
        doc_id,
        reporting_standard="IFRS",
        report_form="balance_sheet",
        is_machine_readable=True,
        page_count=42,
    )
    doc = repo.list_for_publication(pub_id)[0]
    assert doc.reporting_standard == "IFRS"
    assert doc.report_form == "balance_sheet"
    assert doc.is_machine_readable == 1
    assert doc.page_count == 42


def test_classification_check_constraint(
    with_publication: tuple[Database, sqlite3.Connection, str],
) -> None:
    db, conn, pub_id = with_publication
    repo = DocumentsRepo(db, conn)
    repo.add_documents(pub_id, [DocumentInput(relative_path="a.pdf", file_hash="h1")])
    doc_id = repo.list_for_publication(pub_id)[0].document_id
    with pytest.raises(sqlite3.IntegrityError):
        repo.update_classification(
            doc_id,
            reporting_standard="GAAP",  # type: ignore[arg-type]
            report_form="balance_sheet",
            is_machine_readable=True,
            page_count=1,
        )
