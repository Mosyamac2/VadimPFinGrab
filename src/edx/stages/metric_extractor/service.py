"""MetricExtractorService — runs the LLM extraction for one publication."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from edx.config import MetricsConfig
from edx.logging_setup import get_logger
from edx.providers.llm import LLMProvider, LLMRequest, LLMUnavailableError
from edx.stages.metric_extractor.formula import safe_formula_eval
from edx.stages.metric_extractor.models import (
    MetricExtractionItem,
    MetricExtractionResult,
)
from edx.stages.metric_extractor.prompts import build_system_prompt
from edx.stages.metric_extractor.schema import build_metric_extraction_schema
from edx.storage import (
    DocumentRow,
    DocumentsRepo,
    MetricInput,
    MetricsRepo,
    PublicationRow,
    PublicationsRepo,
)

UNIT_MULTIPLIER: Final[dict[str, int]] = {
    "ones": 1,
    "thousands": 1_000,
    "millions": 1_000_000,
    "billions": 1_000_000_000,
}


@dataclass(frozen=True)
class MetricExtractOutcome:
    publication_id: str
    rows_written: int
    extracted_count: int
    requested_count: int
    coverage_ratio: float
    is_incomplete: bool
    skipped_reason: str | None = None


class MetricExtractorService:
    """Drives one LLM extraction per publication.

    The service is provider-agnostic: it talks only to :class:`LLMProvider`
    and never imports ``anthropic`` / ``openai`` / ``httpx`` directly.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        publications_repo: PublicationsRepo,
        documents_repo: DocumentsRepo,
        metrics_repo: MetricsRepo,
        *,
        metrics_config: MetricsConfig,
        raw_dir: Path,
        processed_dir: Path,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        completeness_threshold: float = 0.5,
    ) -> None:
        self.llm_provider = llm_provider
        self.publications_repo = publications_repo
        self.documents_repo = documents_repo
        self.metrics_repo = metrics_repo
        self.metrics_config = metrics_config
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.completeness_threshold = completeness_threshold
        self._json_schema = build_metric_extraction_schema(metrics_config)
        self._system_prompt = build_system_prompt(metrics_config)
        self._log = get_logger("edx.stages.metric_extractor")

    async def run(
        self, publications: Iterable[PublicationRow]
    ) -> list[MetricExtractOutcome]:
        outcomes: list[MetricExtractOutcome] = []
        for pub in publications:
            try:
                outcome = await self._extract_one(pub)
            except LLMUnavailableError as exc:
                self._log.error(
                    "metric_extract_llm_unavailable",
                    publication_id=pub.publication_id,
                    error=str(exc),
                )
                self.publications_repo.mark_status(
                    pub.publication_id, "failed", error=str(exc)
                )
                continue
            except ValidationError as exc:
                self._log.error(
                    "metric_extract_invalid_response",
                    publication_id=pub.publication_id,
                    error=str(exc),
                )
                self.publications_repo.mark_status(
                    pub.publication_id, "failed", error=str(exc)
                )
                continue
            except Exception as exc:  # noqa: BLE001 — fail-soft per ТЗ §14
                self._log.error(
                    "metric_extract_failed",
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

    async def _extract_one(self, pub: PublicationRow) -> MetricExtractOutcome:
        documents = self.documents_repo.list_for_publication(pub.publication_id)
        chosen, chosen_standard = self._pick_documents(documents)
        if not chosen:
            self._log.warning(
                "metric_extract_no_eligible_docs",
                publication_id=pub.publication_id,
            )
            self.publications_repo.mark_status(
                pub.publication_id,
                "skipped",
                error="no IFRS or RSBU document available",
            )
            return MetricExtractOutcome(
                publication_id=pub.publication_id,
                rows_written=0,
                extracted_count=0,
                requested_count=0,
                coverage_ratio=0.0,
                is_incomplete=False,
                skipped_reason="no IFRS or RSBU document",
            )

        primary_doc = chosen[0]
        request = self._build_request(pub, chosen, chosen_standard)
        self._log.info(
            "metric_extract_start",
            publication_id=pub.publication_id,
            standard=chosen_standard,
            docs=[d.relative_path for d in chosen],
            sends_pdf=request.pdf_bytes is not None,
        )

        response = await self.llm_provider.complete(request)
        result = MetricExtractionResult.model_validate(response.data)

        if not result.extractions:
            self._log.warning(
                "metric_extract_no_periods",
                publication_id=pub.publication_id,
            )
            # No periods → publication is incomplete but not failed.
            self.publications_repo.mark_incomplete(pub.publication_id, True)
            self.documents_repo.set_primary_for_publication(
                pub.publication_id, primary_doc.document_id
            )
            return MetricExtractOutcome(
                publication_id=pub.publication_id,
                rows_written=0,
                extracted_count=0,
                requested_count=len(self.metrics_config.metrics),
                coverage_ratio=0.0,
                is_incomplete=True,
                skipped_reason="LLM returned no extractions",
            )

        rows, extracted_count, requested_count = self._build_metric_rows(
            pub, primary_doc, result
        )
        self.metrics_repo.replace_for_publication(pub.publication_id, rows)
        self.documents_repo.set_primary_for_publication(
            pub.publication_id, primary_doc.document_id
        )

        coverage_ratio = (
            extracted_count / requested_count if requested_count else 0.0
        )
        is_incomplete = coverage_ratio < self.completeness_threshold
        self.publications_repo.mark_incomplete(
            pub.publication_id, is_incomplete
        )

        self._log.info(
            "metric_extract_completed",
            publication_id=pub.publication_id,
            standard=chosen_standard,
            periods=len(result.extractions),
            rows_written=len(rows),
            extracted=extracted_count,
            requested=requested_count,
            coverage_ratio=round(coverage_ratio, 3),
            is_incomplete=is_incomplete,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        return MetricExtractOutcome(
            publication_id=pub.publication_id,
            rows_written=len(rows),
            extracted_count=extracted_count,
            requested_count=requested_count,
            coverage_ratio=coverage_ratio,
            is_incomplete=is_incomplete,
        )

    def _pick_documents(
        self, documents: list[DocumentRow]
    ) -> tuple[list[DocumentRow], str]:
        for standard in self.metrics_config.reporting_priority:
            chosen = [d for d in documents if d.reporting_standard == standard]
            if chosen:
                return chosen, standard
        return [], ""

    def _build_request(
        self,
        pub: PublicationRow,
        chosen: list[DocumentRow],
        standard: str,
    ) -> LLMRequest:
        primary_doc = chosen[0]
        full_path = (
            self.raw_dir / pub.ticker / pub.publication_id / primary_doc.relative_path
        )

        send_pdf = (
            self.llm_provider.supports_pdf_input
            and len(chosen) == 1
            and primary_doc.is_machine_readable == 1
            and full_path.is_file()
        )

        if send_pdf:
            pdf_bytes: bytes | None = full_path.read_bytes()
            user_text = (
                f"Эмитент: {pub.ticker}. Стандарт: {standard}. "
                f"Извлеки финансовые показатели из приложенного документа."
            )
        else:
            pdf_bytes = None
            user_text = self._assemble_user_text(pub, chosen, standard)

        return LLMRequest(
            system=self._system_prompt,
            user_text=user_text,
            pdf_bytes=pdf_bytes,
            json_schema=self._json_schema,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            schema_name="extract_metrics",
            schema_description=(
                "Структурированные финансовые показатели по периодам"
            ),
        )

    def _assemble_user_text(
        self,
        pub: PublicationRow,
        chosen: list[DocumentRow],
        standard: str,
    ) -> str:
        sections: list[str] = [
            f"Эмитент: {pub.ticker}. Стандарт отчётности: {standard}.",
        ]
        for doc in chosen:
            if not doc.text_extract_path:
                continue
            extract_path = self.processed_dir / doc.text_extract_path
            if not extract_path.is_file():
                self._log.warning(
                    "metric_extract_missing_text_file",
                    publication_id=pub.publication_id,
                    document_id=doc.document_id,
                    path=str(extract_path),
                )
                continue
            try:
                payload = json.loads(extract_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                self._log.warning(
                    "metric_extract_corrupt_text_file",
                    publication_id=pub.publication_id,
                    document_id=doc.document_id,
                    error=str(exc),
                )
                continue
            sections.append(f"\n=== Документ: {doc.relative_path} ===")
            for page in payload.get("pages", []):
                sections.append(
                    f"--- page {page.get('page_number')} ---"
                )
                sections.append(page.get("text", ""))
        return "\n".join(sections)

    def _build_metric_rows(
        self,
        pub: PublicationRow,
        primary_doc: DocumentRow,
        result: MetricExtractionResult,
    ) -> tuple[list[MetricInput], int, int]:
        rows: list[MetricInput] = []
        spec_by_name = {m.canonical_name: m for m in self.metrics_config.metrics}
        formulas = {
            m.canonical_name: m.formula
            for m in self.metrics_config.metrics
            if m.formula
        }

        extracted_count = 0
        requested_count = 0

        for period in result.extractions:
            normalized: dict[str, float | None] = {}
            quotes: dict[str, str | None] = {}
            for canonical, spec in spec_by_name.items():
                requested_count += 1
                item = period.metrics.get(canonical) or MetricExtractionItem()
                normalized[canonical] = normalize_value(
                    item.value, period.unit, spec.unit
                )
                quotes[canonical] = item.source_quote

            # Apply formulas only for metrics that came back as null but whose
            # formula inputs are present.
            for canonical, formula in formulas.items():
                if normalized.get(canonical) is not None:
                    continue
                derived = safe_formula_eval(formula, normalized)
                if derived is not None:
                    normalized[canonical] = derived
                    quotes[canonical] = (
                        f"derived via formula: {formula}"
                    )

            for canonical, spec in spec_by_name.items():
                value = normalized.get(canonical)
                if value is not None:
                    extracted_count += 1
                rows.append(
                    MetricInput(
                        ticker=pub.ticker,
                        reporting_date=period.reporting_date,
                        period_type=period.period_type,
                        reporting_standard=period.reporting_standard,
                        metric_name=canonical,
                        value=value,
                        currency=period.currency,
                        unit=spec.unit,
                        source_document_id=primary_doc.document_id,
                        qa_warning=None,
                    )
                )
        return rows, extracted_count, requested_count


def normalize_value(
    value: float | None,
    source_unit: str,
    target_unit: str,
) -> float | None:
    """Convert ``value`` from ``source_unit`` to ``target_unit``.

    Both units must be one of ``ones``/``thousands``/``millions``/``billions``.
    Returns ``None`` if ``value`` is None.
    """
    if value is None:
        return None
    if source_unit not in UNIT_MULTIPLIER:
        raise ValueError(f"unknown source unit: {source_unit!r}")
    if target_unit not in UNIT_MULTIPLIER:
        raise ValueError(f"unknown target unit: {target_unit!r}")
    return value * UNIT_MULTIPLIER[source_unit] / UNIT_MULTIPLIER[target_unit]
