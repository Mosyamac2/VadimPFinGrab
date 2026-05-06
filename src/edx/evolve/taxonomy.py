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
    "cli_startup_error",
    "discoverer_403_servicepipe",
    "discoverer_id_not_found",
    "discoverer_no_publications",
    "no_recent_publications",
    "period_unparseable",
    "classifier_other",
    "extract_text_too_short",
    "pipeline_timeout",
    "oom_kill",
    "llm_arg_too_long",
    "llm_credits_exhausted",
    "llm_failed_stuck",
    "all_terminal_no_metrics",
    "metric_coverage_zero",
    "metric_coverage_low",
    "unique_constraint",
    "pipeline_crashed",
    "unknown",
]

_HINTS: Final[dict[TaxonomyCode, str]] = {
    "cli_startup_error": (
        "Pipeline subprocess exited with a non-zero code before writing ANY log "
        "events. The pipeline.log is empty. Most likely cause: argparse rejected "
        "an unknown CLI argument (returncode=2) — e.g. the `update` subcommand "
        "was missing --config-dir or --ticker. Check src/edx/cli.py: ensure the "
        "`update` subparser registers all arguments the runner passes."
    ),
    "discoverer_403_servicepipe": (
        "Discoverer hit ServicePipe (status 403). Verify cookies in "
        "config/app.yaml or switch http_backend to playwright."
    ),
    "discoverer_id_not_found": (
        "All 4 type URLs returned HTTP 404/410 — the e_disclosure_id does not "
        "exist on the portal. Run tools/find_e_disclosure_ids.py (it respects "
        "http_backend: playwright to bypass ServicePipe) to search for an "
        "alternative ID. If no candidates are found, the company is not "
        "registered on e-disclosure.ru and should be removed from the "
        "tracked list (e-disclosure-companies.csv)."
    ),
    "discoverer_no_publications": (
        "Discoverer found zero publications across all 4 type URLs (portal "
        "returned HTTP 200 but the file table was empty). The company exists "
        "on the portal but has no filed reports. The e_disclosure_id is valid "
        "and the pipeline ran cleanly. The verdict is now 'ok' (stable "
        "no-data state), so the ticker enters the normal cooldown cycle "
        "and is re-checked every 7+ days rather than re-selected on every "
        "tick. No code fix is needed. Operator should verify whether the "
        "company has unfulfilled filing obligations; if it will never file, "
        "remove it from e-disclosure-companies.csv."
    ),
    "no_recent_publications": (
        "Discoverer found publications on the website but all predate the "
        "backfill cutoff — company is likely inactive/defunct. The bootstrap "
        "fix (discoverer _BOOTSTRAP_CUTOFF) will import its full archive on "
        "the next tick. If still neutral after that, consider manual_blacklist."
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
    "pipeline_timeout": (
        "Pipeline subprocess was killed (returncode=-1) before reaching the "
        "Metric Extractor stage. Text extraction completed but metric "
        "extraction never ran. Two complementary fixes: "
        "(1) Raise DEFAULT_PIPELINE_TIMEOUT_S in src/edx/evolve/tick.py so "
        "large bootstrapping batches have enough wall-time to finish "
        "(current default: 12 h; systemd TimeoutStartSec=13h is the hard cap). "
        "(2) If the log shows many tesseract_retry_won events with primary_chars "
        "> 800 and improvement < 2%, the OCR retry is doubling processing time "
        "on long narrative pages. Raise tesseract_retry_max_chars in OCRConfig "
        "(src/edx/config/ocr_config.py, default 800) so the retry only fires on "
        "short cover/title pages where PSM choice matters."
    ),
    "oom_kill": (
        "Pipeline subprocess was killed by the OS OOM killer (returncode=-9) "
        "during text extraction. Before Patch 80, convert_from_path rendered "
        "ALL pages of a PDF into RAM simultaneously at 400 DPI — a 100-page "
        "document consumed ~4.6 GB peak. The fix (Patch 80) in "
        "src/edx/stages/text_extractor/ocr/tesseract.py processes pages one "
        "at a time (first_page/last_page), bounding peak memory to ~46 MB per "
        "page. Do NOT revert to bulk convert_from_path — it will OOM again."
    ),
    "llm_arg_too_long": (
        "Metric Extractor failed to spawn the claude subprocess with OSError "
        "[Errno 7] Argument list too long. Linux MAX_ARG_STRLEN (128 KB) limits "
        "each individual argv string; for large RSBU documents assembled from "
        "100+ pages of extracted text (~576 KB UTF-8), passing the user prompt as "
        "a positional CLI arg to `claude -p` exceeds this limit. "
        "Fix: in src/edx/providers/llm/claude_code_provider.py _run_claude(), "
        "remove `user` from the argv list and pass it via stdin instead: "
        "add `stdin=asyncio.subprocess.PIPE` to create_subprocess_exec, and "
        "change proc.communicate() to proc.communicate(input=user.encode('utf-8')). "
        "The `claude -p` CLI reads from stdin when no positional [prompt] is given."
    ),
    "llm_credits_exhausted": (
        "Metric Extractor attempted to call the LLM but both the primary provider "
        "(Anthropic) and the fallback (OpenRouter) returned HTTP 402 Insufficient "
        "Credits. No metrics were extracted. This is an external infrastructure "
        "issue — the operator must add credits to the LLM account(s) before "
        "metric extraction can succeed. After credits are restored, the pipeline "
        "will automatically retry all affected publications (they remain in "
        "'extracted' status thanks to the tick-88 fix)."
    ),
    "llm_failed_stuck": (
        "Publications are permanently stuck in 'failed' status from a prior run "
        "where metric extraction hit HTTP 402 (Insufficient Credits). The "
        "orchestrator only feeds publications in 'extracted' status to the "
        "metric_extractor — so these publications are silently skipped every run "
        "even after LLM credits are restored. The tick-88 fix adds "
        "reset_llm_unavailable_to_extracted() to PublicationsRepo and calls it in "
        "the orchestrator before the metric_extractor stage, so stuck publications "
        "are unblocked automatically. Also: metric_extractor.service no longer "
        "marks publications as 'failed' on LLMUnavailableError — they stay in "
        "'extracted' for retry."
    ),
    "all_terminal_no_metrics": (
        "All publications are in terminal processing states (written or skipped) "
        "but 0 metrics were extracted. 'Written' publications were processed by "
        "the LLM which found no financial metrics — typically scanned documents "
        "(image-only PDFs) with poor OCR quality, or documents with no tabular "
        "financial data (e.g. auditor reports, explanatory notes). 'Skipped' "
        "publications had no documents matching the reporting profile "
        "(IFRS/RSBU/ISSUER) — typically annual reports (type 2) or appendices "
        "without financial statement tables. This is a stable terminal state: "
        "no code fix can extract metrics from non-financial documents or scanned "
        "images with no recoverable text. On the next run the verdict will be 'ok' "
        "(all-terminal-no-metrics branch in verdict.py), placing the company on "
        "the normal 7-day cooldown cycle."
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
    pipeline_returncode: int = 0,
) -> list[TaxonomyEntry]:
    """Return one entry per failing ticker (in input order).

    ``state_slice`` is a mapping ``ticker → {publications, documents,
    metrics, qa_issues}`` produced by :mod:`edx.evolve.bundle`.
    ``pipeline_returncode`` is the exit code of the pipeline subprocess
    (0 if unknown); used to distinguish OOM kills (-9) from timeouts (-1).
    """
    log_lines = _read_log_lines(log_path)
    out: list[TaxonomyEntry] = []
    for ticker in failing_tickers:
        slice_for_ticker = state_slice.get(ticker, {})
        code, evidence = _classify_one(
            ticker, log_lines, slice_for_ticker, pipeline_returncode
        )
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
    pipeline_returncode: int = 0,
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

    # 0. Pipeline never wrote any log events — subprocess crashed at startup
    #    before configure()/logging was initialised.  Argparse exits with
    #    code 2 when an unknown argument is passed (e.g. `update` subparser
    #    missing --config-dir / --ticker); ImportErrors and missing config
    #    dirs also land here.  The signal is: no global log lines at all AND
    #    the state slice for this ticker is completely empty (no publications,
    #    no documents) — nothing was processed.
    if (
        not log_lines
        and not slice_for_ticker.get("publications")
        and not slice_for_ticker.get("documents")
    ):
        return "cli_startup_error", (
            f"pipeline.log empty for {ticker}; no publications or documents in state — "
            "subprocess exited before writing any log events",
        )

    # 1. ServicePipe 403 — dominant signal.
    sp_lines = [
        line for line in ticker_logs
        if line.get("event") == "discoverer_non_200" and line.get("status") == 403
    ]
    if sp_lines:
        return "discoverer_403_servicepipe", _trim_evidence(sp_lines)

    # 2. Discoverer found nothing across all types.
    #    Distinguish HTTP 404/410 (ID doesn't exist on the portal — use
    #    discoverer_id_not_found) from HTTP 200 with an empty file table
    #    (ID is valid, company just has no filed reports — discoverer_no_publications).
    no_pub_lines = [
        line
        for line in ticker_logs
        if line.get("event") == "discoverer_no_publications_for_type"
    ]
    if len(no_pub_lines) >= 4:
        evidence = _trim_evidence(no_pub_lines)
        _not_found_statuses = {404, 410}
        all_http_not_found = all(
            _safe_int(line.get("status")) in _not_found_statuses
            for line in no_pub_lines
        )
        if all_http_not_found:
            return "discoverer_id_not_found", evidence
        return "discoverer_no_publications", evidence

    # 2.5. Company has publications on the website but all predate the cutoff
    #      (new=0 for every type that found anything). The company is likely
    #      inactive/defunct. The bootstrap-cutoff fix will pick them up next run.
    discovered_with_pubs = [
        line for line in ticker_logs
        if line.get("event") == "ticker_type_discovered"
        and _safe_int(line.get("found")) > 0
    ]
    all_new_zero = bool(discovered_with_pubs) and all(
        _safe_int(line.get("new")) == 0 for line in discovered_with_pubs
    )
    pubs_empty = not slice_for_ticker.get("publications")
    if all_new_zero and pubs_empty:
        return "no_recent_publications", _trim_evidence(discovered_with_pubs)

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

    # 4. Period parser stuck.  Only genuine period-related warnings fire this;
    #    structural row warnings ("row with only N cells") are excluded so they
    #    don't misclassify inactive-company batches as period failures.
    period_lines = [
        line
        for line in ticker_logs
        if line.get("event") == "period_parser_unmatched"
        or (
            line.get("event") == "discoverer_parse_warning"
            and "reporting period" in str(line.get("detail", "")).lower()
        )
    ]
    if period_lines:
        return "period_unparseable", _trim_evidence(period_lines)

    # 4.5. Pipeline killed before metric extraction ran.
    #      Detects: text extraction events exist for this ticker in the log
    #      but zero metric_extract_* events — the pipeline was killed after
    #      the text_extractor stage but before metric_extractor started.
    #      Distinguishes OOM kill (returncode=-9) from wall-clock timeout
    #      (returncode=-1).  Default returncode=0 falls back to pipeline_timeout
    #      for backward compatibility when the caller doesn't supply it.
    #      metric_extract events use ``publication_id`` not ``ticker``, so we
    #      match by ``publication_id.startswith(ticker + "-")``.
    pub_extracted_lines = [
        line
        for line in log_lines
        if line.get("event") == "publication_extracted"
        and isinstance(line.get("publication_id"), str)
        and str(line["publication_id"]).startswith(ticker + "-")
    ]
    metric_started_lines = [
        line
        for line in log_lines
        if isinstance(line.get("event"), str)
        and str(line["event"]).startswith("metric_extract")
        and isinstance(line.get("publication_id"), str)
        and str(line["publication_id"]).startswith(ticker + "-")
    ]
    if pub_extracted_lines and not metric_started_lines:
        if pipeline_returncode == -9:
            return "oom_kill", _trim_evidence(pub_extracted_lines[:3])
        return "pipeline_timeout", _trim_evidence(pub_extracted_lines[:3])

    # 4.6. Publications stuck in 'failed' status from a prior HTTP 402 run.
    #      When metric_extractor previously marked publications as 'failed' on
    #      LLMUnavailableError, those publications are skipped by the
    #      orchestrator on every subsequent run — even after credits are
    #      restored — because the orchestrator only feeds 'extracted'
    #      publications to the metric_extractor.  Fires when the state slice
    #      shows failed publications with HTTP 402 in last_error AND no
    #      metric_extract_* events are present in the log (the metric_extractor
    #      never ran because there were no 'extracted' publications to process).
    #      Placed before rule 4.75 so the more specific stuck-state diagnosis
    #      takes precedence over the general credits-exhausted hint.
    _raw_pubs = slice_for_ticker.get("publications")
    _pubs_list: list[object] = _raw_pubs if isinstance(_raw_pubs, list) else []
    stuck_llm_pubs = [
        p
        for p in _pubs_list
        if isinstance(p, dict)
        and p.get("status") == "failed"
        and isinstance(p.get("last_error"), str)
        and "HTTP 402" in str(p["last_error"])
    ]
    if stuck_llm_pubs and not metric_started_lines:
        evidence = tuple(
            json.dumps(
                {
                    "publication_id": p.get("publication_id"),
                    "status": p.get("status"),
                    "last_error": str(p.get("last_error", ""))[:120],
                },
                ensure_ascii=False,
            )
            for p in stuck_llm_pubs[:5]
        )
        return "llm_failed_stuck", evidence

    # 4.65. LLM subprocess spawn failed with OSError [Errno 7] — user prompt
    #       passed as a CLI arg exceeds Linux MAX_ARG_STRLEN (128 KB).
    #       Placed before 4.75 so the E2BIG-specific hint takes precedence
    #       over the generic credits-exhausted hint.
    arg_too_long_lines = [
        line
        for line in log_lines
        if line.get("event") == "metric_extract_llm_unavailable"
        and isinstance(line.get("publication_id"), str)
        and str(line["publication_id"]).startswith(ticker + "-")
        and "Argument list too long" in str(line.get("error", ""))
    ]
    if arg_too_long_lines:
        return "llm_arg_too_long", _trim_evidence(arg_too_long_lines)

    # 4.75. LLM provider out of credits — metric_extract_llm_unavailable events
    #       exist for this ticker's publications (HTTP 402 on both primary and
    #       fallback providers). Placed before rule 5 so the operator sees the
    #       actionable "add LLM credits" hint instead of the misleading
    #       "extend synonyms" hint from metric_coverage_zero.
    llm_unavail_lines = [
        line
        for line in log_lines
        if line.get("event") == "metric_extract_llm_unavailable"
        and isinstance(line.get("publication_id"), str)
        and str(line["publication_id"]).startswith(ticker + "-")
    ]
    if llm_unavail_lines:
        return "llm_credits_exhausted", _trim_evidence(llm_unavail_lines)

    # 4.8. All publications in terminal states (written or skipped) with 0 metrics.
    #      Fires when the state slice shows ALL publications are in {written, skipped}
    #      AND metrics_count == 0. This is a stable terminal state — no code fix can
    #      produce metrics from non-financial documents or scanned images.  Placed
    #      before rule 5 (metric_coverage_zero) to suppress the misleading "extend
    #      synonyms" hint when the actual cause is non-financial documents (annual
    #      reports, appendices) or poor OCR on old scanned RSBU filings.
    _metrics_count_48 = _safe_int(slice_for_ticker.get("metrics_count"))
    if _pubs_list and _metrics_count_48 == 0:
        _terminal = {"written", "skipped"}
        _all_terminal = all(
            isinstance(p, dict) and p.get("status") in _terminal
            for p in _pubs_list
        )
        if _all_terminal:
            _n_written = sum(
                1 for p in _pubs_list
                if isinstance(p, dict) and p.get("status") == "written"
            )
            _n_skipped = sum(
                1 for p in _pubs_list
                if isinstance(p, dict) and p.get("status") == "skipped"
            )
            return "all_terminal_no_metrics", (
                f"{len(_pubs_list)} publications all terminal: "
                f"written={_n_written}, skipped={_n_skipped}, metrics=0",
            )

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
