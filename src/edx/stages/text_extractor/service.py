"""Text Extractor stage: produce per-document JSON with page text + tables."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
