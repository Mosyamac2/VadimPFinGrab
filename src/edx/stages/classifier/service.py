"""Classifier stage: enrich ``documents`` with detected fields, mark publication."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from edx.config import MetricsConfig
from edx.logging_setup import get_logger
from edx.stages.classifier.heuristics import (
    ReportForm,
    ReportingStandardWithOther,
    detect_report_form,
    detect_reporting_standard,
)
from edx.stages.classifier.pdf_inspector import (
    count_pages,
    extract_first_pages_text,
    is_machine_readable,
)
from edx.storage import (
    DocumentRow,
    DocumentsRepo,
    PublicationRow,
    PublicationsRepo,
)

PDF_MIME_PREFIXES = ("application/pdf",)
PDF_SUFFIXES = (".pdf",)


@dataclass(frozen=True)
class ClassifyOutcome:
    publication_id: str
    pdf_count: int
    machine_readable_count: int
    scan_count: int
    standards: dict[str, int]


class ClassifierService:
    def __init__(
        self,
        publications_repo: PublicationsRepo,
        documents_repo: DocumentsRepo,
        *,
        raw_dir: Path,
        metrics_config: MetricsConfig,
        min_text_chars: int = 400,
        first_pages_to_inspect: int = 3,
    ) -> None:
        self.publications_repo = publications_repo
        self.documents_repo = documents_repo
        self.raw_dir = Path(raw_dir)
        self.metrics_config = metrics_config
        self.min_text_chars = min_text_chars
        self.first_pages_to_inspect = first_pages_to_inspect
        self._log = get_logger("edx.stages.classifier")

    def run(
        self, publications: Iterable[PublicationRow]
    ) -> list[ClassifyOutcome]:
        outcomes: list[ClassifyOutcome] = []
        for pub in publications:
            try:
                outcome = self._classify_one(pub)
            except Exception as exc:  # noqa: BLE001 — fail-soft per ТЗ §14
                self._log.error(
                    "classifier_failed",
                    publication_id=pub.publication_id,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
                self.publications_repo.mark_status(
                    pub.publication_id, "failed", error=str(exc)
                )
                continue
            outcomes.append(outcome)
        return outcomes

    def _classify_one(self, pub: PublicationRow) -> ClassifyOutcome:
        pub_dir = self.raw_dir / pub.ticker / pub.publication_id
        documents = self.documents_repo.list_for_publication(pub.publication_id)

        pdf_count = 0
        machine_readable = 0
        scan_count = 0
        standards: dict[str, int] = {"IFRS": 0, "RSBU": 0, "OTHER": 0}

        for doc in documents:
            if not _is_pdf(doc):
                continue
            pdf_count += 1
            full_path = pub_dir / doc.relative_path
            if not full_path.exists():
                self._log.warning(
                    "classifier_missing_file",
                    publication_id=pub.publication_id,
                    document_id=doc.document_id,
                    path=str(full_path),
                )
                continue

            try:
                pages = count_pages(full_path)
            except Exception as exc:  # noqa: BLE001 — broken PDF: skip doc, keep going
                self._log.warning(
                    "classifier_unreadable_pdf",
                    publication_id=pub.publication_id,
                    document_id=doc.document_id,
                    error=str(exc),
                )
                continue

            mr = is_machine_readable(
                full_path,
                min_text_chars=self.min_text_chars,
                pages=self.first_pages_to_inspect,
            )
            if mr:
                machine_readable += 1
                text = extract_first_pages_text(
                    full_path, pages=self.first_pages_to_inspect
                )
                standard: ReportingStandardWithOther = detect_reporting_standard(
                    text, self.metrics_config
                )
                form: ReportForm = detect_report_form(text)
            else:
                scan_count += 1
                standard = "OTHER"
                form = "other"

            standards[standard] = standards.get(standard, 0) + 1

            self.documents_repo.update_classification(
                doc.document_id,
                reporting_standard=standard,
                report_form=form,
                is_machine_readable=mr,
                page_count=pages,
            )

        self.publications_repo.mark_status(pub.publication_id, "classified")
        self._log.info(
            "publication_classified",
            publication_id=pub.publication_id,
            pdf_count=pdf_count,
            machine_readable=machine_readable,
            scans=scan_count,
            standards=standards,
        )
        return ClassifyOutcome(
            publication_id=pub.publication_id,
            pdf_count=pdf_count,
            machine_readable_count=machine_readable,
            scan_count=scan_count,
            standards=standards,
        )


def _is_pdf(doc: DocumentRow) -> bool:
    if doc.mime_type and any(
        doc.mime_type.startswith(prefix) for prefix in PDF_MIME_PREFIXES
    ):
        return True
    return doc.relative_path.lower().endswith(PDF_SUFFIXES)
