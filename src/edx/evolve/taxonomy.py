"""Auto-classify per-ticker failure modes for the Diagnostic Bundle (Patch 41).

The classifier walks ``pipeline.log`` (JSON lines) plus a pre-computed
state-slice (publications/documents/metrics/qa_issues for the failing
tickers) and emits one :class:`TaxonomyEntry` per failing ticker. The
codes are intentionally coarse so that the agent's prompt can route
hint-by-hint, not on a single regex.

The taxonomy never *prescribes* a fix — it only narrows the
hypothesis space. Patch 42's slash-command turns these hints into a
prompt section.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

TaxonomyCode = Literal[
    "discoverer_403_servicepipe",
    "discoverer_no_publications",
    "period_unparseable",
    "classifier_other",
    "extract_text_too_short",
    "metric_coverage_zero",
    "metric_coverage_low",
    "unique_constraint",
    "pipeline_crashed",
    "unknown",
]

_HINTS: Final[dict[TaxonomyCode, str]] = {
    "discoverer_403_servicepipe": (
        "Discoverer hit ServicePipe (status 403). Verify cookies in "
        "config/app.yaml or switch http_backend to playwright."
    ),
    "discoverer_no_publications": (
        "Discoverer found zero publications across all 4 type URLs. "
        "The e_disclosure_id may be invalid; cross-check via "
        "tools/find_e_disclosure_ids.py."
    ),
    "period_unparseable": (
        "Discoverer reached the listing but could not parse "
        "reporting_period_year/type. Extend regexes in "
        "src/edx/stages/discoverer/period.py."
    ),
    "classifier_other": (
        "All documents were classified as reporting_standard='OTHER'. "
        "Check the URL→standard mapping in classifier/heuristics.py "
        "and the per-page classifier output."
    ),
    "extract_text_too_short": (
        "Text Extractor returned <1000 chars. Tesseract DPI/PSM may "
        "be off, vision_fallback may be needed (Patch 33), or the "
        "document is encrypted/empty."
    ),
    "metric_coverage_zero": (
        "Metric Extractor processed the doc but extracted 0 metrics. "
        "Issuer-specific terminology missing from config/metrics.yaml "
        "synonyms — extend the relevant profile section."
    ),
    "metric_coverage_low": (
        "0 < coverage < 50% — partial extraction. Either expand "
        "synonyms or add an aggregation_hint for sums of multiple "
        "RSBU rows."
    ),
    "unique_constraint": (
        "metric_extract_failed with UNIQUE constraint on metrics. "
        "Patch 26 dedup may have regressed; check for duplicate "
        "extractions in the LLM response and the dedup code in "
        "metric_extractor/service.py."
    ),
    "pipeline_crashed": (
        "Pipeline subprocess returned non-zero or hit an exception. "
        "See pipeline.log.errors for the traceback."
    ),
    "unknown": (
        "No pre-computed pattern matched — but you have full agency to "
        "diagnose. Do NOT escalate; this is exactly the case the loop "
        "exists for. Investigation kit (all in evolution/runs/<tick>/): "
        "(1) pipeline.log — full structured JSON event stream, grep by "
        "ticker; (2) pipeline.log.errors — pre-filtered error/exception "
        "lines; (3) state-slice.json — DB rows for the 3 tickers "
        "(publications/documents/metrics/qa_issues); (4) snap_before/"
        "snap_after.json — what the pipeline produced. Workflow: grep "
        "the log for the failing ticker, identify the *first* failing "
        "stage (discoverer/classifier/text_extractor/metric_extractor), "
        "read the relevant src/edx/stages/ module, formulate a fix. If "
        "the failure is a genuinely new class, ALSO add a new code + "
        "_HINTS entry + matching branch in src/edx/evolve/taxonomy.py "
        "_classify_one so the next occurrence has a head-start."
    ),
}


@dataclass(frozen=True, slots=True)
class TaxonomyEntry:
    ticker: str
    code: TaxonomyCode
    evidence: tuple[str, ...]
    hint: str


def classify_failures(
    log_path: Path,
    state_slice: dict[str, dict[str, object]],
    failing_tickers: list[str],
) -> list[TaxonomyEntry]:
    """Return one entry per failing ticker (in input order).

    ``state_slice`` is a mapping ``ticker → {publications, documents,
    metrics, qa_issues}`` produced by :mod:`edx.evolve.bundle`.
    """
    log_lines = _read_log_lines(log_path)
    out: list[TaxonomyEntry] = []
    for ticker in failing_tickers:
        slice_for_ticker = state_slice.get(ticker, {})
        code, evidence = _classify_one(ticker, log_lines, slice_for_ticker)
        out.append(
            TaxonomyEntry(
                ticker=ticker,
                code=code,
                evidence=evidence,
                hint=_HINTS[code],
            )
        )
    return out


def _read_log_lines(log_path: Path) -> list[dict[str, object]]:
    """Best-effort JSON-lines reader; malformed lines are skipped."""
    if not log_path.exists():
        return []
    parsed: list[dict[str, object]] = []
    with log_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict):
                parsed.append(obj)
    return parsed


def _classify_one(
    ticker: str,
    log_lines: list[dict[str, object]],
    slice_for_ticker: dict[str, object],
) -> tuple[TaxonomyCode, tuple[str, ...]]:
    """Return (code, up-to-5 evidence strings) for a single ticker.

    Per-ticker events (e.g. ``discoverer_non_200``) are matched only when
    their ``ticker`` field equals ``ticker``. Crash detection
    (``pipeline_crashed``) is the only path that may consult untagged
    log lines — everything else respects the per-ticker boundary so we
    don't smear one company's failure onto its batch siblings.
    """

    ticker_logs = [
        line
        for line in log_lines
        if isinstance(line.get("ticker"), str) and line["ticker"] == ticker
    ]

    # 1. ServicePipe 403 — dominant signal.
    sp_lines = [
        line for line in ticker_logs
        if line.get("event") == "discoverer_non_200" and line.get("status") == 403
    ]
    if sp_lines:
        return "discoverer_403_servicepipe", _trim_evidence(sp_lines)

    # 2. Discoverer found nothing across all types.
    no_pub_count = sum(
        1
        for line in ticker_logs
        if line.get("event") == "discoverer_no_publications_for_type"
    )
    if no_pub_count >= 4:
        evidence = _trim_evidence(
            [
                line
                for line in ticker_logs
                if line.get("event") == "discoverer_no_publications_for_type"
            ]
        )
        return "discoverer_no_publications", evidence

    # 3. UNIQUE constraint failures from metric extractor.
    uniq_lines: list[dict[str, object]] = []
    for line in ticker_logs:
        if line.get("event") != "metric_extract_failed":
            continue
        err = line.get("error")
        if isinstance(err, str) and "UNIQUE constraint" in err:
            uniq_lines.append(line)
    if uniq_lines:
        return "unique_constraint", _trim_evidence(uniq_lines)

    # 4. Period parser stuck.
    period_lines = [
        line
        for line in ticker_logs
        if line.get("event") in {"period_parser_unmatched", "discoverer_parse_warning"}
    ]
    if period_lines:
        return "period_unparseable", _trim_evidence(period_lines)

    # 5. Coverage signals from state.
    metrics_rows = _safe_int(slice_for_ticker.get("metrics_count"))
    qa_codes_present = _qa_codes_set(slice_for_ticker.get("qa_issues"))
    if "incomplete" in qa_codes_present:
        if metrics_rows == 0:
            return "metric_coverage_zero", (
                f"qa_issues code=incomplete on {ticker}; metrics rows=0",
            )
        return "metric_coverage_low", (
            f"qa_issues code=incomplete on {ticker}; metrics rows={metrics_rows}",
        )

    if metrics_rows == 0 and _has_machine_readable(slice_for_ticker):
        return "metric_coverage_zero", (
            f"machine_readable docs but 0 metrics extracted for {ticker}",
        )

    # 6. Classifier said OTHER for all documents.
    docs_raw = slice_for_ticker.get("documents")
    docs: list[dict[str, object]] = (
        [d for d in docs_raw if isinstance(d, dict)]
        if isinstance(docs_raw, list)
        else []
    )
    if docs:
        kinds = {d.get("reporting_standard") for d in docs}
        if kinds == {"OTHER"}:
            return "classifier_other", (
                f"all {len(docs)} documents marked reporting_standard=OTHER",
            )

    # 7. Text extractor too short — surrogate via short documents.
    short_docs = sum(
        1
        for d in docs
        if isinstance(d.get("text_extract_path"), str)
        and _safe_int(d.get("page_count")) > 0
        and _safe_int(d.get("text_pages_count")) == 0
    )
    if docs and short_docs == len(docs):
        return "extract_text_too_short", (
            f"all {short_docs} documents had 0 text pages",
        )

    # 8. Subprocess crashed — error/exception in the global log.
    crash_lines = [
        line
        for line in log_lines
        if line.get("level") == "error"
        and isinstance(line.get("exc_type"), str)
    ]
    if crash_lines:
        return "pipeline_crashed", _trim_evidence(crash_lines)

    return "unknown", ()


_EVIDENCE_FIELDS: Final[tuple[str, ...]] = (
    "event",
    "level",
    "ticker",
    "publication_id",
    "url",
    "status",
    "detail",
    "error",
    "code",
    "message",
)


def _trim_evidence(lines: list[dict[str, object]]) -> tuple[str, ...]:
    """Compress the first 5 matching log lines into short readable strings."""
    out: list[str] = []
    for raw in lines[:5]:
        keep = {
            field: raw[field]
            for field in _EVIDENCE_FIELDS
            if field in raw
        }
        out.append(json.dumps(keep, ensure_ascii=False, sort_keys=True))
    return tuple(out)


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


_QA_CODE_RE = re.compile(r"\bcode\s*[:=]\s*([\w_]+)", re.IGNORECASE)


def _qa_codes_set(qa_field: object) -> set[str]:
    """Try to extract code values from various legitimate shapes:

    - a list of dicts with 'code' key (state-slice format),
    - a flat string we may have stored.
    """
    out: set[str] = set()
    if isinstance(qa_field, list):
        for item in qa_field:
            if isinstance(item, dict) and isinstance(item.get("code"), str):
                out.add(item["code"])
    elif isinstance(qa_field, str):
        for match in _QA_CODE_RE.finditer(qa_field):
            out.add(match.group(1))
    return out


def _has_machine_readable(slice_for_ticker: dict[str, object]) -> bool:
    docs = slice_for_ticker.get("documents")
    if not isinstance(docs, list):
        return False
    return any(
        isinstance(d, dict) and _safe_int(d.get("is_machine_readable")) >= 1
        for d in docs
    )


__all__ = ["TaxonomyCode", "TaxonomyEntry", "classify_failures"]
