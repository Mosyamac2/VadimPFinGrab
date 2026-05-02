"""Classifier stage: enrich ``documents`` with detected fields, mark publication.

Patch 18 reframes the per-document ``is_machine_readable`` boolean as the
*aggregate* of a per-page classification (≥1 text page → 1; otherwise 0)
and stores the full per-page list in ``documents.pages_classification`` so
the Text Extractor can OCR scanned pages even inside an otherwise text PDF.
"""

from __future__ import annotations

import json
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
    reporting_standard_for_type_code,
)
from edx.stages.classifier.pdf_inspector import (
    DEFAULT_MIN_TEXT_CHARS_PER_PAGE,
    PageClassification,
    classify_pages,
    extract_first_pages_text,
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
        min_text_chars_per_page: int = DEFAULT_MIN_TEXT_CHARS_PER_PAGE,
        first_pages_to_inspect: int = 3,
    ) -> None:
        self.publications_repo = publications_repo
        self.documents_repo = documents_repo
        self.raw_dir = Path(raw_dir)
        self.metrics_config = metrics_config
        self.min_text_chars_per_page = min_text_chars_per_page
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
        # Patch 21: ISSUER and ANNUAL join the counter; ``defaultdict``-like
        # ``.get(..., 0)`` below handles unseen keys gracefully.
        standards: dict[str, int] = {
            "IFRS": 0, "RSBU": 0, "OTHER": 0, "ISSUER": 0, "ANNUAL": 0
        }

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
                page_classifications = classify_pages(
                    full_path,
                    min_text_chars_per_page=self.min_text_chars_per_page,
                )
            except Exception as exc:  # noqa: BLE001 — broken PDF: skip doc, keep going
                self._log.warning(
                    "classifier_unreadable_pdf",
                    publication_id=pub.publication_id,
                    document_id=doc.document_id,
                    error=str(exc),
                )
                continue

            pages = len(page_classifications)
            text_pages = [p for p in page_classifications if p.kind == "text"]
            scan_pages = [p for p in page_classifications if p.kind == "scan"]
            # Aggregate flag stays the same shape as before Patch 18 so
            # downstream call-sites that only check ``is_machine_readable``
            # keep working: any text page counts the document as readable.
            mr = bool(text_pages)
            if mr:
                machine_readable += 1
                text = extract_first_pages_text(
                    full_path, pages=self.first_pages_to_inspect
                )
                # Patch 21: prefer the deterministic listing-URL type_code
                # over text heuristics. The Discoverer attaches it on
                # discovery (Patch 16); only legacy rows from before
                # Patch 17 fall through to the text-based detector.
                from_url = reporting_standard_for_type_code(
                    pub.report_type_code
                )
                standard: ReportingStandardWithOther
                if from_url is not None:
                    standard = from_url
                    heuristic = detect_reporting_standard(
                        text, self.metrics_config
                    )
                    if (
                        heuristic in ("IFRS", "RSBU")
                        and standard in ("IFRS", "RSBU")
                        and heuristic != standard
                    ):
                        # Operator wants to know if the file under "type=4
                        # МСФО" has no IFRS markers (mis-filing on the
                        # portal). type=2/5 are looser by design (annual
                        # reports / issuer reports include MD&A text), so
                        # we don't second-guess them.
                        self._log.warning(
                            "classifier_type_code_disagrees_with_text",
                            publication_id=pub.publication_id,
                            document_id=doc.document_id,
                            from_type_code=standard,
                            from_text=heuristic,
                        )
                else:
                    standard = detect_reporting_standard(
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
                pages_classification=_serialize_pages(page_classifications),
                text_pages_count=len(text_pages),
                scan_pages_count=len(scan_pages),
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


def _serialize_pages(pages: list[PageClassification]) -> str:
    """JSON encode the per-page classification for storage."""
    return json.dumps(
        [
            {"page": p.page_index, "chars": p.char_count, "kind": p.kind}
            for p in pages
        ],
        ensure_ascii=False,
    )
