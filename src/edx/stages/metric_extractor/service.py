"""MetricExtractorService — runs the LLM extraction for one publication.

Patch 19 routes each publication through its issuer's profile (bank vs
non-bank) and tailors the LLM prompt + JSON schema to the (profile,
source_standard) pair: the prompt skips metrics that don't apply to the
chosen source (``only_in_sources``) and adds RSBU-only aggregation hints
where the metric spans several balance lines.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from edx.config import (
    MetricsConfig,
    MetricsProfile,
    ReportingStandard,
    TickersConfig,
)
from edx.logging_setup import get_logger
from edx.providers.llm import LLMProvider, LLMRequest, LLMUnavailableError
from edx.stages.metric_extractor.models import (
    MetricExtractionItem,
    MetricExtractionResult,
)
from edx.stages.metric_extractor.prompts import build_system_prompt
from edx.stages.metric_extractor.schema import build_metric_extraction_schema
from edx.stages.text_extractor.issuer_trim import extract_section_1_4
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

# Targeting normalised values, the storage layer always stores ``ones``.
TARGET_UNIT: Final[str] = "ones"


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
    """Drives one LLM extraction per publication."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        publications_repo: PublicationsRepo,
        documents_repo: DocumentsRepo,
        metrics_repo: MetricsRepo,
        *,
        metrics_config: MetricsConfig,
        tickers_config: TickersConfig,
        raw_dir: Path,
        processed_dir: Path,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        completeness_threshold: float = 0.5,
        issuer_trim_max_chars: int = 30_000,
    ) -> None:
        self.llm_provider = llm_provider
        self.publications_repo = publications_repo
        self.documents_repo = documents_repo
        self.metrics_repo = metrics_repo
        self.metrics_config = metrics_config
        self.tickers_config = tickers_config
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.completeness_threshold = completeness_threshold
        self.issuer_trim_max_chars = issuer_trim_max_chars
        # Cache prompt+schema per (profile, source_standard). At most
        # 2 profiles × 3 standards = 6 entries.
        self._prompt_cache: dict[tuple[str, str], str] = {}
        self._schema_cache: dict[tuple[str, str], dict[str, object]] = {}
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
        ticker_entry = self.tickers_config.find(pub.ticker)
        profile_name = ticker_entry.profile if ticker_entry else "non_bank"
        profile = self.metrics_config.for_profile(profile_name)

        documents = self.documents_repo.list_for_publication(pub.publication_id)
        chosen, chosen_standard = self._pick_documents(documents, profile)
        if not chosen:
            self._log.warning(
                "metric_extract_no_eligible_docs",
                publication_id=pub.publication_id,
                profile=profile_name,
            )
            self.publications_repo.mark_status(
                pub.publication_id,
                "skipped",
                error=(
                    "no document matches profile reporting_priority "
                    f"{profile.reporting_priority}"
                ),
            )
            return MetricExtractOutcome(
                publication_id=pub.publication_id,
                rows_written=0,
                extracted_count=0,
                requested_count=0,
                coverage_ratio=0.0,
                is_incomplete=False,
                skipped_reason="no eligible document for profile",
            )

        primary_doc = chosen[0]
        # ``_pick_documents`` only returns ``None`` for the standard when
        # ``chosen`` is empty, which is handled above; reassure mypy here.
        assert chosen_standard is not None
        request = self._build_request(pub, chosen, profile, chosen_standard)
        self._log.info(
            "metric_extract_start",
            publication_id=pub.publication_id,
            profile=profile_name,
            standard=chosen_standard,
            docs=[d.relative_path for d in chosen],
            sends_pdf=request.pdf_bytes is not None,
        )

        response = await self.llm_provider.complete(request)
        result = MetricExtractionResult.model_validate(response.data)

        applicable_metrics = self._applicable_metric_names(
            profile, chosen_standard
        )

        if not result.extractions:
            self._log.warning(
                "metric_extract_no_periods",
                publication_id=pub.publication_id,
            )
            self.publications_repo.mark_incomplete(pub.publication_id, True)
            self.documents_repo.set_primary_for_publication(
                pub.publication_id, primary_doc.document_id
            )
            return MetricExtractOutcome(
                publication_id=pub.publication_id,
                rows_written=0,
                extracted_count=0,
                requested_count=len(applicable_metrics),
                coverage_ratio=0.0,
                is_incomplete=True,
                skipped_reason="LLM returned no extractions",
            )

        rows, extracted_count, requested_count = self._build_metric_rows(
            pub, primary_doc, result, profile, applicable_metrics
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
            profile=profile_name,
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
        self,
        documents: list[DocumentRow],
        profile: MetricsProfile,
    ) -> tuple[list[DocumentRow], ReportingStandard | None]:
        for standard in profile.reporting_priority:
            chosen = [d for d in documents if d.reporting_standard == standard]
            if chosen:
                return chosen, standard
        return [], None

    def _applicable_metric_names(
        self, profile: MetricsProfile, source_standard: ReportingStandard
    ) -> list[str]:
        return [
            name
            for name, spec in profile.metrics.items()
            if not spec.only_in_sources or source_standard in spec.only_in_sources
        ]

    def _prompt_for(
        self, profile_name: str, source_standard: ReportingStandard
    ) -> str:
        key = (profile_name, source_standard)
        cached = self._prompt_cache.get(key)
        if cached is not None:
            return cached
        prompt = build_system_prompt(
            self.metrics_config.for_profile(profile_name),  # type: ignore[arg-type]
            source_standard=source_standard,
        )
        self._prompt_cache[key] = prompt
        return prompt

    def _schema_for(
        self, profile_name: str, source_standard: ReportingStandard
    ) -> dict[str, object]:
        key = (profile_name, source_standard)
        cached = self._schema_cache.get(key)
        if cached is not None:
            return cached
        schema = build_metric_extraction_schema(
            self.metrics_config.for_profile(profile_name),  # type: ignore[arg-type]
            source_standard=source_standard,
        )
        self._schema_cache[key] = schema
        return schema

    def _build_request(
        self,
        pub: PublicationRow,
        chosen: list[DocumentRow],
        profile: MetricsProfile,
        standard: ReportingStandard,
    ) -> LLMRequest:
        del profile  # only the cached (profile_name, standard) keying is used
        ticker_entry = self.tickers_config.find(pub.ticker)
        profile_name = ticker_entry.profile if ticker_entry else "non_bank"

        primary_doc = chosen[0]
        full_path = (
            self.raw_dir / pub.ticker / pub.publication_id / primary_doc.relative_path
        )

        # Patch 21: Issuer Reports go through the trimmed-text path
        # always — sending the full 60+ page PDF when only section 1.4
        # carries KPIs is wasted tokens.
        send_pdf = (
            self.llm_provider.supports_pdf_input
            and len(chosen) == 1
            and primary_doc.is_machine_readable == 1
            and full_path.is_file()
            and standard != "ISSUER"
        )

        if send_pdf:
            pdf_bytes: bytes | None = full_path.read_bytes()
            user_text = (
                f"Эмитент: {pub.ticker} (профиль {profile_name}). "
                f"Стандарт: {standard}. "
                f"Извлеки финансовые показатели из приложенного документа."
            )
        else:
            pdf_bytes = None
            user_text = self._assemble_user_text(
                pub, chosen, standard, profile_name
            )

        return LLMRequest(
            system=self._prompt_for(profile_name, standard),
            user_text=user_text,
            pdf_bytes=pdf_bytes,
            json_schema=self._schema_for(profile_name, standard),
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
        standard: ReportingStandard,
        profile_name: str,
    ) -> str:
        sections: list[str] = [
            f"Эмитент: {pub.ticker} (профиль {profile_name}). "
            f"Стандарт отчётности: {standard}.",
        ]
        if standard == "ISSUER":
            # Patch 21: nudge the LLM toward the KPI table; the trimmed
            # slice below already contains exactly that section.
            sections.append(
                "Перед тобой раздел 1.4 «Основные финансовые показатели» "
                "ежеквартального отчёта эмитента. Извлекай значения только "
                "из сводных KPI-таблиц этого раздела; не пытайся достроить "
                "то, чего нет."
            )
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
            doc_text = "\n".join(
                f"--- page {page.get('page_number')} ---\n"
                f"{page.get('text', '')}"
                for page in payload.get("pages", [])
            )
            if standard == "ISSUER":
                trim = extract_section_1_4(
                    doc_text, max_chars=self.issuer_trim_max_chars
                )
                for warning in trim.warnings:
                    self._log.warning(
                        "metric_extract_issuer_trim",
                        publication_id=pub.publication_id,
                        document_id=doc.document_id,
                        detail=warning,
                    )
                if trim.content is not None:
                    self._log.info(
                        "metric_extract_issuer_section_extracted",
                        publication_id=pub.publication_id,
                        document_id=doc.document_id,
                        anchor_label=trim.anchor_label_seen,
                        end_anchor=trim.end_anchor_seen,
                        chars=len(trim.content),
                    )
                    doc_text = trim.content
                # ``trim.content is None`` → fall back to the full doc_text
                # (graceful degradation, with the warning above logged).
            sections.append(f"\n=== Документ: {doc.relative_path} ===")
            sections.append(doc_text)
        return "\n".join(sections)

    def _build_metric_rows(
        self,
        pub: PublicationRow,
        primary_doc: DocumentRow,
        result: MetricExtractionResult,
        profile: MetricsProfile,
        applicable_metrics: list[str],
    ) -> tuple[list[MetricInput], int, int]:
        # Patch 21: storage now accepts ISSUER along with IFRS/RSBU (see
        # migration 0009 + the widened ``ReportingStandard`` Literal),
        # so the Patch-19 ISSUER→RSBU compatibility shim is gone — we
        # write ``period.reporting_standard`` straight through.
        #
        # Patch 26: dedup before flushing to the repo. The LLM occasionally
        # emits two ``extractions`` entries with the same
        # ``(reporting_date, period_type, reporting_standard)`` — typically
        # a "current" period and a "comparative prior" period stamped with
        # the same date stub, or two entries for the same period rendered
        # twice in different sections of the document. The metrics table
        # has a UNIQUE(ticker, reporting_date, period_type,
        # reporting_standard, metric_name) constraint, and a naive
        # bulk-insert raises IntegrityError, killing the publication.
        # Strategy: keep one row per ``(date, period_type, std,
        # metric_name)`` key — prefer the entry with a non-null ``value``;
        # if both are non-null, the later one wins (LLM tends to put the
        # most-recent period last).
        deduped: dict[
            tuple[str, str, str, str], MetricInput
        ] = {}
        seen_keys_per_period: dict[
            tuple[str, str, str], int
        ] = {}
        requested_count = 0

        for period in result.extractions:
            period_key = (
                period.reporting_date,
                period.period_type,
                period.reporting_standard,
            )
            seen_keys_per_period[period_key] = (
                seen_keys_per_period.get(period_key, 0) + 1
            )
            for canonical in applicable_metrics:
                requested_count += 1
                spec = profile.metrics[canonical]
                item = period.metrics.get(canonical) or MetricExtractionItem()
                normalized_value = normalize_value(
                    item.value, period.unit, TARGET_UNIT
                )
                row = MetricInput(
                    ticker=pub.ticker,
                    reporting_date=period.reporting_date,
                    period_type=period.period_type,
                    reporting_standard=period.reporting_standard,
                    metric_name=canonical,
                    value=normalized_value,
                    currency=period.currency,
                    unit=spec.unit,
                    source_document_id=primary_doc.document_id,
                    qa_warning=None,
                )
                key = (*period_key, canonical)
                existing = deduped.get(key)
                if existing is None:
                    deduped[key] = row
                elif existing.value is None and row.value is not None:
                    deduped[key] = row  # prefer non-null over null
                elif existing.value is not None and row.value is not None:
                    deduped[key] = row  # both filled — last one wins
                # else: keep existing (both null, or new is null)

        # If any period appeared more than once, log it so the operator
        # can spot suspicious LLM output without digging through state.
        for dup_key, count in seen_keys_per_period.items():
            if count > 1:
                self._log.warning(
                    "metric_extract_duplicate_period",
                    publication_id=pub.publication_id,
                    reporting_date=dup_key[0],
                    period_type=dup_key[1],
                    reporting_standard=dup_key[2],
                    duplicate_count=count,
                )

        rows = list(deduped.values())
        extracted_count = sum(1 for r in rows if r.value is not None)
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
