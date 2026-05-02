"""Text Extractor stage: produce per-document JSON with page text + tables.

Patch 18 adds *hybrid* extraction: when the Classifier marks a document as
machine-readable but its ``pages_classification`` lists scan pages too
(typical for banking RSBU forms 0409806/0409807 — text narrative followed
by scanned regulator forms), we keep the native text for the readable
pages and OCR only the scanned ones. Pages are merged in natural order
so the LLM downstream sees a continuous document.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pymupdf

from edx.logging_setup import get_logger
from edx.stages.text_extractor.models import PageText, Table
from edx.stages.text_extractor.native import extract_tables, extract_text
from edx.stages.text_extractor.normalize import normalize_text
from edx.stages.text_extractor.ocr.base import OCRProvider
from edx.storage import (
    DocumentRow,
    DocumentsRepo,
    PublicationRow,
    PublicationsRepo,
)


@dataclass(frozen=True)
class ExtractOutcome:
    publication_id: str
    documents_processed: int
    native_count: int
    ocr_count: int
    total_chars: int
    truncated: bool
    # Patch 18: documents that went through the native+partial-OCR path.
    # Counted on top of ``native_count`` (a hybrid is still primarily native).
    hybrid_count: int = 0


class TextExtractorService:
    def __init__(
        self,
        publications_repo: PublicationsRepo,
        documents_repo: DocumentsRepo,
        ocr_provider: OCRProvider,
        *,
        raw_dir: Path,
        processed_dir: Path,
        ocr_langs: list[str],
        max_chars: int = 400_000,
        extract_tables_enabled: bool = True,
        header_footer_min_pages: int = 3,
    ) -> None:
        self.publications_repo = publications_repo
        self.documents_repo = documents_repo
        self.ocr_provider = ocr_provider
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.ocr_langs = ocr_langs
        self.max_chars = max_chars
        self.extract_tables_enabled = extract_tables_enabled
        self.header_footer_min_pages = header_footer_min_pages
        self._log = get_logger("edx.stages.text_extractor")

    def run(
        self, publications: Iterable[PublicationRow]
    ) -> list[ExtractOutcome]:
        outcomes: list[ExtractOutcome] = []
        for pub in publications:
            try:
                outcome = self._extract_one(pub)
            except Exception as exc:  # noqa: BLE001 — fail-soft per ТЗ §14
                self._log.error(
                    "text_extractor_failed",
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

    def _extract_one(self, pub: PublicationRow) -> ExtractOutcome:
        documents = self.documents_repo.list_for_publication(pub.publication_id)
        pub_raw_dir = self.raw_dir / pub.ticker / pub.publication_id
        pub_processed_dir = self.processed_dir / pub.ticker / pub.publication_id
        pub_processed_dir.mkdir(parents=True, exist_ok=True)

        native = 0
        ocr = 0
        hybrid = 0
        total_chars = 0
        truncated_any = False
        processed = 0

        for doc in documents:
            if not _is_pdf(doc):
                continue
            if doc.is_machine_readable is None:
                self._log.warning(
                    "text_extractor_unclassified_pdf",
                    publication_id=pub.publication_id,
                    document_id=doc.document_id,
                )
                continue

            full_path = pub_raw_dir / doc.relative_path
            if not full_path.exists():
                self._log.warning(
                    "text_extractor_missing_file",
                    publication_id=pub.publication_id,
                    document_id=doc.document_id,
                    path=str(full_path),
                )
                continue

            if doc.is_machine_readable == 1:
                scan_indices_0 = _scan_indices(doc)
                if scan_indices_0:
                    pages, method = self._extract_hybrid(
                        full_path, scan_indices_0
                    )
                    self._log.info(
                        "text_extractor_ocr_partial",
                        publication_id=pub.publication_id,
                        document_id=doc.document_id,
                        scan_pages=len(scan_indices_0),
                        text_pages=doc.text_pages_count,
                    )
                    native += 1
                    hybrid += 1
                else:
                    pages, method = self._extract_native(full_path)
                    native += 1
            else:
                pages, method = self._extract_ocr(full_path)
                ocr += 1

            cleaned_pages = self._normalize(pages)
            truncated_pages, was_truncated = self._enforce_max_chars(cleaned_pages)
            if was_truncated:
                truncated_any = True
                self._log.warning(
                    "text_extractor_truncated",
                    publication_id=pub.publication_id,
                    document_id=doc.document_id,
                    max_chars=self.max_chars,
                )

            doc_chars = sum(len(p.text) for p in truncated_pages)
            total_chars += doc_chars

            relative_target = (
                Path(pub.ticker) / pub.publication_id / f"{doc.document_id}.json"
            )
            target = self.processed_dir / relative_target
            self._write_extract_json(target, method, truncated_pages)
            self.documents_repo.set_text_extract_path(
                doc.document_id, str(relative_target)
            )
            processed += 1

        self.publications_repo.mark_status(pub.publication_id, "extracted")
        self._log.info(
            "publication_extracted",
            publication_id=pub.publication_id,
            documents_processed=processed,
            native=native,
            ocr=ocr,
            hybrid=hybrid,
            total_chars=total_chars,
            truncated=truncated_any,
        )
        return ExtractOutcome(
            publication_id=pub.publication_id,
            documents_processed=processed,
            native_count=native,
            ocr_count=ocr,
            total_chars=total_chars,
            truncated=truncated_any,
            hybrid_count=hybrid,
        )

    def _extract_native(
        self, pdf_path: Path
    ) -> tuple[list[PageText], str]:
        text_pages = extract_text(pdf_path)
        if not self.extract_tables_enabled:
            return text_pages, "native"
        tables_by_page: dict[int, list[Table]] = {
            page_no: tables for page_no, tables in extract_tables(pdf_path)
        }
        merged = [
            PageText(
                page_number=page.page_number,
                text=page.text,
                tables=tables_by_page.get(page.page_number) or None,
            )
            for page in text_pages
        ]
        return merged, "native"

    def _extract_ocr(
        self, pdf_path: Path
    ) -> tuple[list[PageText], str]:
        pages = self.ocr_provider.recognize(pdf_path, self.ocr_langs)
        return pages, f"ocr_{self.ocr_provider.name}"

    def _extract_hybrid(
        self, pdf_path: Path, scan_indices_0: list[int]
    ) -> tuple[list[PageText], str]:
        """Native text for readable pages + OCR for the listed scan pages.

        ``scan_indices_0`` is 0-based, sorted natural order. We build a
        temporary single-PDF containing just those pages, route it through
        the configured OCR provider, then splice the recognised text back
        into the native page list at matching ``page_number``.
        """
        native_pages, _ = self._extract_native(pdf_path)
        ocr_text_by_idx0 = self._ocr_subset_by_index(pdf_path, scan_indices_0)
        merged: list[PageText] = []
        for page in native_pages:
            idx0 = page.page_number - 1
            if idx0 in ocr_text_by_idx0:
                merged.append(
                    PageText(
                        page_number=page.page_number,
                        text=ocr_text_by_idx0[idx0],
                        tables=None,
                    )
                )
            else:
                merged.append(page)
        return merged, f"native+ocr_{self.ocr_provider.name}"

    def _ocr_subset_by_index(
        self, pdf_path: Path, scan_indices_0: list[int]
    ) -> dict[int, str]:
        """OCR exactly the pages at ``scan_indices_0`` (0-based) in the PDF.

        Returns a ``{original_index_0 → text}`` map. Builds a temporary
        sub-PDF so the existing :class:`OCRProvider` contract
        (``recognize(pdf, langs) → all pages``) keeps working without
        per-page extensions.
        """
        if not scan_indices_0:
            return {}
        sub_doc = pymupdf.open()  # type: ignore[no-untyped-call]
        src_doc = pymupdf.open(str(pdf_path))  # type: ignore[no-untyped-call]
        try:
            for idx in scan_indices_0:
                sub_doc.insert_pdf(  # type: ignore[no-untyped-call]
                    src_doc, from_page=idx, to_page=idx
                )
            with tempfile.NamedTemporaryFile(
                suffix=".pdf", delete=False
            ) as tmp_fh:
                tmp_path = Path(tmp_fh.name)
            sub_doc.save(str(tmp_path))  # type: ignore[no-untyped-call]
        finally:
            sub_doc.close()  # type: ignore[no-untyped-call]
            src_doc.close()  # type: ignore[no-untyped-call]
        try:
            ocr_pages = self.ocr_provider.recognize(tmp_path, self.ocr_langs)
            # ``ocr_pages`` come back numbered 1..len(scan_indices_0). Map
            # each result back to its original 0-based index in the source.
            return {
                scan_indices_0[i]: ocr_pages[i].text
                for i in range(min(len(ocr_pages), len(scan_indices_0)))
            }
        finally:
            tmp_path.unlink(missing_ok=True)

    def _normalize(self, pages: list[PageText]) -> list[PageText]:
        cleaned_texts = normalize_text(
            [p.text for p in pages],
            header_footer_min_pages=self.header_footer_min_pages,
        )
        return [
            PageText(
                page_number=page.page_number,
                text=cleaned_text,
                tables=page.tables,
            )
            for page, cleaned_text in zip(pages, cleaned_texts, strict=True)
        ]

    def _enforce_max_chars(
        self, pages: list[PageText]
    ) -> tuple[list[PageText], bool]:
        out: list[PageText] = []
        budget = self.max_chars
        truncated = False
        for page in pages:
            if budget <= 0:
                truncated = True
                break
            if len(page.text) <= budget:
                out.append(page)
                budget -= len(page.text)
                continue
            out.append(
                PageText(
                    page_number=page.page_number,
                    text=page.text[:budget],
                    tables=page.tables,
                )
            )
            budget = 0
            truncated = True
            break
        return out, truncated

    def _write_extract_json(
        self,
        target: Path,
        extraction_method: str,
        pages: list[PageText],
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "extraction_method": extraction_method,
            "extracted_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "pages": [
                {
                    "page_number": p.page_number,
                    "text": p.text,
                    **({"tables": p.tables} if p.tables else {}),
                }
                for p in pages
            ],
        }
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(target)


def _is_pdf(doc: DocumentRow) -> bool:
    if doc.mime_type and doc.mime_type.startswith("application/pdf"):
        return True
    return doc.relative_path.lower().endswith(".pdf")


def _scan_indices(doc: DocumentRow) -> list[int]:
    """Pull the 0-based scan-page indices out of the JSON column.

    Returns an empty list when the document was classified before Patch 18
    (``pages_classification`` is ``None``) — those documents fall back to
    the pre-Patch-18 "all native" or "all OCR" behaviour.
    """
    raw = doc.pages_classification
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [
        int(entry["page"])
        for entry in parsed
        if isinstance(entry, dict)
        and entry.get("kind") == "scan"
        and "page" in entry
    ]
