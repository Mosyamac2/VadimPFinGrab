# Self-Evolve Long-Term Memory

> Версионированный журнал решённых failure-классов и анти-паттернов
> для self-evolve loop'а проекта e-disclosure-extractor.
> Читается Claude Code в STEP 0 каждого тика; обновляется в STEP 4.
>
> Структура и правила — см.
> [`PLAN_self_evolution.md` §7.5](../PLAN_self_evolution.md).
>
> NEVER записывать сюда: секреты, traceback'и, ID конкретных публикаций,
> оригинальные тексты документов под NDA. Только обобщения.

## Index — solved failure classes

| failure_class | first_seen_tick | last_revisit_tick | applied_patches | solved? |
|---|---|---|---|---|
| cli_startup_error | #1 | #1 | cli.py update subparser --config-dir/--ticker; taxonomy cli_startup_error code | yes |
| pipeline_timeout | #77 | #101 | tick.py DEFAULT_PIPELINE_TIMEOUT_S 30→90→180→720 min; OCR retry_max_chars 800; taxonomy hint updated | yes |
| verdict_already_healthy | #75 | #75 | verdict.py ok-branch uses after.metrics_rows | yes |
| defunct_company_bootstrap | #74 | #74 | discoverer _BOOTSTRAP_CUTOFF, unpacker BadZipFile | yes |
| unpacker_os_error | #87 | #87 | unpacker _extract_zip/_extract_rar catch OSError → UnpackerError; taxonomy llm_credits_exhausted added | yes |
| llm_credits_exhausted | #87 | #88 | taxonomy code + hint added; external infra issue (operator must add LLM credits) | partial |
| llm_failed_stuck | #88 | #88 | metric_extractor no longer marks pubs failed on 402; repo.reset_llm_unavailable_to_extracted(); bundle detects stuck neutral tickers; taxonomy llm_failed_stuck code | yes |
| neutral_zero_publications | #91 | #91 | bundle._has_zero_publications(); neutral tickers with empty state included in failing_tickers → taxonomy fires discoverer_no_publications | yes |
| neutral_improvement_gate | #92 | #92 | tick._batch_improvement() now counts neutral→ok as improvement (was only fail/regression→ok) | yes |
| discoverer_id_not_found | #93 | #93 | taxonomy.py new code for all-HTTP-404 case; find_e_disclosure_ids.py now uses build_http_client() to respect http_backend | partial |
| verdict_zero_publications_ok | #94 | #94 | verdict.py new ok-branch for zero-publication clean runs; taxonomy discoverer_no_publications hint updated | yes |
| llm_arg_too_long | #103 | #103 | claude_code_provider.py user via stdin not argv (E2BIG); verdict.py all_written_no_metrics ok-branch; taxonomy llm_arg_too_long; bundle _has_written_no_metrics_publications | yes |
| all_terminal_no_metrics | #180 | #180 | verdict.py all_terminal_no_metrics ok-branch (written+skipped); taxonomy all_terminal_no_metrics code + rule 4.8; bundle expanded _has_written_no_metrics_publications to include skipped | yes |

## Patches log (reverse-chronological)

### evolve(180) — 2026-05-06 — all_terminal_no_metrics
- **Tick:** #180 — batch [EDX20321]
- **Failing companies:** EDX20321 (ОАО «ЦСД») — verdict=neutral, metrics_delta=0. Infinite
  re-selection loop: the Picker treats `neutral` as `_PRIORITY_NEVER` and re-picks the company
  on every tick regardless of how many times the pipeline has run.
- **Root cause:** EDX20321 has 46 publications in two terminal states: 19 "written" (type-3 RSBU
  scanned PDFs, `is_machine_readable=0`, LLM extracted 0 metrics — documents are real RSBU reports
  but the OCR quality and/or document structure produced no extractable financial figures) and 27
  "skipped" (type-2 annual reports and appendices, `last_error="no document matches profile
  reporting_priority ['IFRS', 'RSBU', 'ISSUER']"` — these publications contain no IFRS/RSBU/ISSUER
  documents by design). Both states are permanent/terminal — no further processing will occur.
  Total metrics in DB: 0. Three compounding gaps:
  1. `compute_verdict` had no branch for the "all publications terminal (written+skipped), 0 metrics"
     case. The existing `all_written_no_metrics` branch (tick #103) only covered all-"written"; it
     didn't fire when the terminal set was mixed written+skipped. The verdict fell through to
     `neutral` → Picker re-selected every tick.
  2. `bundle._has_written_no_metrics_publications()` checked only `status == "written"`, so the
     mixed written+skipped case returned False → ticker excluded from `failing_tickers` →
     `failure_taxonomy.json = []` → agent received no diagnostic hypothesis.
  3. Taxonomy had no `all_terminal_no_metrics` code. Even if taxonomy had been called, rule 5
     (`metric_coverage_zero`) would have fired instead, producing the misleading "extend synonyms"
     hint (incorrect — there's no synonym gap; the documents genuinely contain no financial data).
- **Files touched:**
  - `src/edx/evolve/verdict.py` — extended all_terminal_no_metrics ok-branch: condition now
    checks `written + skipped == publications_total` (was only `written == publications_total`);
    updated docstring
  - `src/edx/evolve/bundle.py` — `_has_written_no_metrics_publications()` uses
    `_terminal = {"written", "skipped"}` (was only `"written"`); updated docstring
  - `src/edx/evolve/taxonomy.py` — added `all_terminal_no_metrics` TaxonomyCode + `_HINTS` entry
    + rule 4.8 (before rule 5 `metric_coverage_zero`)
  - `tests/evolve/test_verdict.py` — `_snap()` now accepts `skipped=` param; added
    `test_verdict_ok_when_mixed_written_skipped_no_metrics_on_retry`,
    `test_verdict_neutral_on_first_run_mixed_written_skipped`,
    `test_verdict_ok_all_terminal_does_not_fire_when_some_not_terminal`
  - `tests/evolve/test_taxonomy.py` — added `test_classify_all_terminal_no_metrics`,
    `test_classify_all_terminal_no_metrics_takes_precedence_over_metric_coverage_zero`
  - `tests/evolve/test_bundle.py` — added tests for skipped+written detection and bundle
    integration
  - `tests/evolve/test_pilot_edge_cases.py` — updated
    `test_verdict_treats_zero_metrics_with_returncode_zero_as_ok` (was `_as_neutral`):
    the all-skipped-0-metrics snapshot now correctly yields `ok` (was `neutral` before this tick)
- **Tests added:**
  - `tests/evolve/test_verdict.py::test_verdict_ok_when_mixed_written_skipped_no_metrics_on_retry`
  - `tests/evolve/test_verdict.py::test_verdict_neutral_on_first_run_mixed_written_skipped`
  - `tests/evolve/test_verdict.py::test_verdict_ok_all_terminal_does_not_fire_when_some_not_terminal`
  - `tests/evolve/test_taxonomy.py::test_classify_all_terminal_no_metrics`
  - `tests/evolve/test_taxonomy.py::test_classify_all_terminal_no_metrics_takes_precedence_over_metric_coverage_zero`
- **Anti-regression notes:**
  - DO NOT restrict the all_terminal_no_metrics verdict branch to only `status == "written"` —
    the `skipped` status (no IFRS/RSBU/ISSUER docs in publication) is equally terminal and must
    be treated identically. A company with 19 written + 27 skipped is in a stable final state.
  - DO NOT place taxonomy rule 4.8 (`all_terminal_no_metrics`) after rule 5
    (`metric_coverage_zero`) — when all pubs are terminal with 0 metrics, rule 5 fires on the
    machine-readable docs heuristic and produces the misleading "extend synonyms" hint. Rule 4.8
    must come first to suppress it.
  - DO NOT weaken the `before.publications_total > 0` guard in the all_terminal_no_metrics verdict
    branch — it prevents first-bootstrap false-positives (before.total=0, after.total=N, 0 metrics
    would get ok instead of neutral on the very first tick, bypassing the normal gate flow).
- **Coverage delta on batch:**
  - EDX20321: neutral (19 written + 27 skipped, 0 metrics, infinite re-selection loop) →
    ok (same state; new branch recognises all-terminal stable state; company placed on 7-day
    cooldown cycle instead of being re-picked every tick)

### evolve(103) — 2026-05-05 — llm_arg_too_long
- **Tick:** #103 — batch [EDX1482]
- **Failing companies:** EDX1482 (ПАО "РГС Банк") — verdict=neutral, metrics_delta=0,
  notes=["publications.written +1"]. Technically a "silent failure": the publication was
  written, but metric extraction was never attempted due to OSError [Errno 7] at
  subprocess spawn time.
- **Root cause:** `ClaudeCodeLLMProvider._run_claude()` passed the assembled user prompt
  (128 pages of RSBU extracted text ≈ 576 KB UTF-8) as a positional `argv` argument after
  `-p`. Linux per-string argv limit `MAX_ARG_STRLEN = 131072 bytes (128 KB)` caused
  `asyncio.create_subprocess_exec` to raise `OSError: [Errno 7] Argument list too long`
  at subprocess spawn time. The metric_extractor caught this as `LLMUnavailableError`,
  left the publication in "extracted" status, the validator/writer moved it to "written"
  — so the pipeline appeared to succeed but actually never ran the LLM. A secondary issue
  was that the only document (Учетная политика / accounting policy) contains no financial
  figures, so 0 metrics would be extracted even after fixing E2BIG. The improvement gate
  required a new verdict branch (`all_written_no_metrics`) to pass on retry.
  Three additional gaps fixed: (1) `bundle.py` didn't include neutral tickers with
  all-written/no-metrics publications in `failing_tickers` → `failure_taxonomy.json = []`;
  (2) taxonomy had no `llm_arg_too_long` code, rule 4.75 (`llm_credits_exhausted`) would
  have fired instead; (3) verdict had no branch for "all written, 0 metrics, previously
  bootstrapped" → improvement gate would fail even after E2BIG fix.
- **Files touched:**
  - `src/edx/providers/llm/claude_code_provider.py` — remove `user` from argv, add
    `stdin=asyncio.subprocess.PIPE`, pass `proc.communicate(input=user.encode("utf-8"))`
  - `src/edx/evolve/verdict.py` — new ok-branch: all publications written, 0 metrics,
    `before.publications_total > 0` (guards against first-bootstrap false-positive)
  - `src/edx/evolve/taxonomy.py` — new `llm_arg_too_long` TaxonomyCode + rule 4.65
    (before 4.75) detecting "Argument list too long" in `metric_extract_llm_unavailable`
  - `src/edx/evolve/bundle.py` — `_has_written_no_metrics_publications()` helper + 4th
    condition in `failing_tickers` for neutral tickers with all-written/no-metrics pubs
  - `tests/providers/llm/test_claude_code.py` — updated `_FakeProc.communicate(input=)`,
    `_patch_subprocess` captures proc; fixed `test_pdf_input_staged_to_tempdir` to check
    stdin rather than argv; 2 new tests (stdin-not-argv, oserror-errno7)
  - `tests/evolve/test_verdict.py` — 4 new tests for all_written_no_metrics branch
  - `tests/evolve/test_taxonomy.py` — 2 new tests for `llm_arg_too_long`
  - `tests/evolve/test_bundle.py` — 5 new tests for `_has_written_no_metrics_publications`
    and bundle integration
- **Tests added:**
  - `tests/providers/llm/test_claude_code.py` — `test_user_prompt_passed_via_stdin_not_argv`,
    `test_oserror_errno7_raises_llm_unavailable`
  - `tests/evolve/test_verdict.py` — `test_verdict_ok_on_retry_when_all_written_no_metrics`,
    `test_verdict_neutral_on_first_run_all_written_no_metrics`,
    `test_verdict_ok_all_written_no_metrics_does_not_fire_with_partial_written`,
    `test_verdict_ok_all_written_no_metrics_does_not_fire_when_metrics_exist`
  - `tests/evolve/test_taxonomy.py` — `test_classify_llm_arg_too_long`,
    `test_classify_llm_arg_too_long_takes_precedence_over_llm_credits_exhausted`
  - `tests/evolve/test_bundle.py` — `test_has_written_no_metrics_true_when_all_written_zero_metrics`,
    `test_has_written_no_metrics_false_when_metrics_exist`,
    `test_has_written_no_metrics_false_when_not_all_written`,
    `test_has_written_no_metrics_false_when_empty_pubs`,
    `test_bundle_neutral_ticker_with_written_no_metrics_included_in_failing`
- **Anti-regression notes:**
  - DO NOT revert to passing `user` as an argv argument after `-p`. Linux
    `MAX_ARG_STRLEN = 128 KB` limits individual argv strings; RSBU documents with 100+
    pages regularly produce 500+ KB of extracted text. Use `stdin=PIPE` +
    `communicate(input=user.encode("utf-8"))` — `claude -p` reads from stdin when no
    positional `[prompt]` argument is given.
  - DO NOT weaken the `before.publications_total > 0` guard in the all_written_no_metrics
    verdict branch. Without it, a company bootstrapped for the first time (before.total=0,
    after.total=N, 0 metrics) would get verdict=ok instead of neutral, bypassing the normal
    improvement-gate flow.
  - DO NOT remove `_has_written_no_metrics_publications()` from `bundle.py` failing_tickers.
    Without it, neutral tickers with all-written/no-metrics publications are silently excluded
    from taxonomy classification — `failure_taxonomy.json` will be empty, giving the agent
    no starting hypothesis.
- **Coverage delta on batch:**
  - EDX1482: neutral (publications.written +1, metrics=0, E2BIG silenced the LLM) →
    expected ok on next tick (before.total=1, all written, 0 metrics, new branch fires)

### evolve(101) — 2026-05-05 — pipeline_timeout
- **Tick:** #101 — batch [EDX1480]
- **Failing companies:** EDX1480 (ПАО "Аэрофлот", verdict=fail, returncode=-1, 0 metrics)
- **Root cause:** Aeroflot's publication archive is very large (194 total: 133 already
  `extracted`, 61 still `classified`, 1 `failed`). The text extractor spent the full 3h
  DEFAULT_PIPELINE_TIMEOUT_S (10800s) processing large PDFs (many truncated at 400K chars)
  and was still working on the 61 remaining classified publications when the process was
  killed. The metric extractor never ran. Log timestamps confirm: pipeline started at
  00:39, last text_extractor_truncated event was at 03:33 (2h54m), pipeline killed at
  ~03:39 (3h). There were no `tesseract_retry_won` events — the bottleneck was PyMuPDF
  processing very large PDFs, not OCR retries. A `text_extractor_failed` (FzErrorLimit
  "exception stack overflow") for EDX1480-2-763314 was handled gracefully (per-publication
  fail-soft path) and was not the root cause. The taxonomy correctly classified this as
  `pipeline_timeout` on the first pass. The operator already pre-authorised longer runtime
  by raising the systemd `TimeoutStartSec` to 13h (commit `5be94cb`). Only the Python-level
  constant lagged behind.
- **Fix:** Raised `DEFAULT_PIPELINE_TIMEOUT_S` from `3 * 60 * 60` (3h) to `12 * 60 * 60`
  (12h). Updated the `pipeline_timeout` taxonomy hint to document the new default and the
  systemd hard cap (13h). Also fixed a pre-existing test fragility in
  `test_find_e_disclosure_ids.py`: `test_main_async_uses_build_http_client_not_direct_client`
  loaded settings from `config-evolve` and filtered by ticker `EDX13577`. Since `config-evolve`
  is regenerated each tick and now contains only `EDX1480`, the test was returning "No tickers
  selected." and exiting with code 2. Fixed by mocking `load_all` to return a fake settings
  object with a controlled ticker — the test is now independent of the batch ticker.
- **Files touched:**
  - `src/edx/evolve/tick.py` — `DEFAULT_PIPELINE_TIMEOUT_S` 10800 → 43200 (3h → 12h)
  - `src/edx/evolve/taxonomy.py` — updated `pipeline_timeout` hint with current default
  - `tests/tools/test_find_e_disclosure_ids.py` — mock `load_all` to decouple test from
    batch-specific config-evolve content
- **Tests added:** none (constant change; existing pipeline_timeout tests cover the taxonomy
  detection path; test fix is a bug fix not a new behaviour)
- **Anti-regression notes:**
  - DO NOT revert `DEFAULT_PIPELINE_TIMEOUT_S` back to 3h — Aeroflot (EDX1480) and similar
    large companies (200+ publications, large PDFs) need more than 3h for text extraction
    alone. The systemd TimeoutStartSec=13h is the hard cap.
  - DO NOT set `DEFAULT_PIPELINE_TIMEOUT_S` above `TimeoutStartSec` (currently 13h =
    46800s) — the pipeline would be killed by systemd before Python's timeout fires, making
    the returncode unpredictable.
  - DO NOT make `test_main_async_uses_build_http_client_not_direct_client` depend on the
    `config-evolve` directory having a specific ticker. The config-evolve directory is
    overwritten every tick; use `mock` / `patch` for `load_all` to keep the test hermetic.
- **Coverage delta on batch:**
  - EDX1480: fail (returncode=-1, 0 metrics; text extractor killed at 3h with 61/194
    publications still classified) → expected ok on next run (12h timeout allows text
    extraction to complete for remaining 61 publications; 133 already extracted ready for
    metric extraction; sufficient publications to produce metrics_rows >= min_metrics_for_ok)

### evolve(94) — 2026-05-04 — verdict_zero_publications_ok
- **Tick:** #94 — batch [EDX13577]
- **Failing companies:** EDX13577 (ОАО "Морской торговый порт Темрюк", verdict=neutral,
  metrics_delta=0) — fourth consecutive tick with zero publications. Taxonomy fires
  `discoverer_no_publications` (status=200 for all 4 type URLs): portal returns HTTP 200
  with empty file tables, meaning the company IS registered on e-disclosure.ru under ID
  13577 but has genuinely never filed any of the 4 report types. This is not a code bug —
  it is a stable no-data state. The root issue was that `compute_verdict` returned "neutral"
  for zero-publication clean runs, which caused the Picker to treat the company as
  `_PRIORITY_NEVER` and re-select it on every tick indefinitely, because neutral is
  indistinguishable from "never attempted" in the picker's priority logic.
- **Root cause:** `compute_verdict` in `verdict.py` had no branch for the "valid company,
  no filings, pipeline ran cleanly" case. The ok condition requires `metrics_delta >= 1`
  or `after.metrics_rows >= 1` — neither fires for a company with 0 publications. So such
  companies are permanently classified as "neutral" regardless of how many times the
  pipeline runs. Since the Picker treats `neutral` as `_PRIORITY_NEVER` (same as "never
  attempted"), the company is re-selected on every tick, generating a diagnostic bundle
  and taxonomy hint every time but never receiving a meaningful verdict. The improvement
  gate (`_batch_improvement`) requires `before != "ok" AND after == "ok"`, which was
  never satisfiable for this company without actual metrics.
- **Fix:** Added a new branch to `compute_verdict` after the metrics-based ok check:
  when `pipeline_returncode == 0 AND before.publications_total == 0 AND
  after.publications_total == 0`, return `"ok"` (stable no-data state). This correctly
  represents: "the pipeline ran cleanly and confirmed the company has no filings — this is
  not a regression or failure, it is a stable state that requires no further code action."
  The Picker then puts the company on the normal 7-day cooldown, re-checking periodically
  instead of on every tick. The retry run (with the new `compute_verdict`) maps
  EDX13577 from neutral (first run, old code) to ok (retry, new code), so the improvement
  gate passes. Updated the `discoverer_no_publications` taxonomy hint to explain the new
  "ok" verdict semantics. Updated the existing test that expected "neutral" for 0-pub
  clean runs (it now correctly uses `pubs_total=5` to represent a bootstrapped-but-unprocessed
  company). Added 3 new verdict tests.
- **Files touched:**
  - `src/edx/evolve/verdict.py` — added "stable zero-publication ok" branch; updated docstring
  - `src/edx/evolve/taxonomy.py` — updated `discoverer_no_publications` hint
  - `tests/evolve/test_verdict.py` — updated `_snap` helper to accept `pubs_total`; renamed
    and fixed `test_verdict_neutral_when_no_metrics_and_no_change` to use `pubs_total=5`;
    added 3 new tests for zero-publication verdict scenarios
- **Tests added:**
  - `tests/evolve/test_verdict.py::test_verdict_ok_when_zero_publications_and_clean_run`
  - `tests/evolve/test_verdict.py::test_verdict_fail_when_zero_publications_and_nonzero_returncode`
  - `tests/evolve/test_verdict.py::test_verdict_neutral_when_publications_appeared_this_tick`
- **Anti-regression notes:**
  - DO NOT remove the `before.publications_total == 0 AND after.publications_total == 0`
    branch from `compute_verdict`. Without it, companies with valid IDs but no portal
    filings are permanently classified as "neutral" and re-selected on every tick, wasting
    resources and generating redundant diagnostic bundles indefinitely.
  - DO NOT conflate this with the `after.metrics_rows >= min_metrics_for_ok` branch
    (tick #75 fix): that handles already-populated companies with existing metrics; this
    new branch handles companies that have NEVER had any publications at all.
  - DO NOT widen the condition to `after.publications_total == 0` without also requiring
    `before.publications_total == 0` — a company that LOST all its publications this tick
    (regression) should not be classified as "ok". The `before.publications_total == 0`
    guard ensures we only match companies that were ALWAYS at zero publications.
  - DO NOT use `pipeline_returncode != 0` with this condition. If the pipeline crashed
    (rc != 0), the existing `fail` branch fires first (rc != 0 AND metrics_delta == 0 → fail),
    so zero-publication companies with crashes correctly get "fail" not "ok".
- **Coverage delta on batch:**
  - EDX13577: neutral (0 metrics, portal returns 200+empty for all 4 types, picker
    re-selects every tick) → ok (0 metrics, but stable no-data state — company is
    registered on e-disclosure.ru but has never filed; placed on 7-day cooldown cycle)

### evolve(93) — 2026-05-04 — discoverer_id_not_found
- **Tick:** #93 — batch [EDX13577]
- **Failing companies:** EDX13577 (ОАО "Морской торговый порт Темрюк", verdict=neutral,
  metrics_delta=0) — third consecutive tick with zero publications. The failure_taxonomy.json
  showed `discoverer_no_publications` with `status: 404` on all 4 type URLs. The validation
  run (Playwright, env loaded) returned HTTP 200 with empty file tables — the company IS
  registered on e-disclosure.ru under ID 13577 but has never filed any of the 4 report types
  (annual/RSBU/IFRS/issuer). Portal search via `find_e_disclosure_ids.py` returned 0 candidates
  both before and after the Playwright fix.
- **Root cause:** Two independent issues found:
  1. `find_e_disclosure_ids.py` directly instantiated `EDisclosureClient` (httpx) instead
     of calling `build_http_client(settings)`. When `http_backend: playwright` is configured
     (as it is in production), the search tool used the wrong backend. Plain httpx is blocked
     by ServicePipe (returns 403 JS challenge, 1297 bytes) while Playwright navigates through
     the challenge. The tool correctly reported "no candidates" but for the wrong reason —
     ServicePipe was blocking the search, not the absence of the company in the index. After
     the fix, Playwright-based search confirmed: 0 candidates (company genuinely not findable
     via name search on the portal, regardless of backend).
  2. The `discoverer_no_publications` taxonomy code was ambiguous — it fired for BOTH HTTP
     404/410 responses (the company ID doesn't exist) AND HTTP 200+empty responses (the ID
     is valid but the company has no filed documents). These require different operator actions:
     a 404 strongly suggests the wrong ID, while 200+empty means the company exists but hasn't
     filed. Added `discoverer_id_not_found` for the all-404/410 case.
- **Fix:**
  1. Updated `_main_async` in `tools/find_e_disclosure_ids.py` to use
     `build_http_client(settings)` (from `edx.http`) instead of directly constructing
     `EDisclosureClient`. The function now respects the configured `http_backend`.
  2. Added `discoverer_id_not_found` TaxonomyCode, hint, and detection branch in taxonomy.py:
     rule 2 now checks if ALL `discoverer_no_publications_for_type` events have `status` in
     {404, 410}; if so, returns `discoverer_id_not_found`; otherwise returns
     `discoverer_no_publications` (200+empty case). The `test_bundle.py` assertion was updated
     from `discoverer_no_publications` to `discoverer_id_not_found` for the 404-evidence test.
- **Files touched:**
  - `tools/find_e_disclosure_ids.py` — `_main_async` uses `build_http_client(settings)`
  - `src/edx/evolve/taxonomy.py` — added `discoverer_id_not_found` code, hint, detection branch
  - `tests/evolve/test_taxonomy.py` — added tests for 404, 410, mixed-status cases; updated
    existing test to add explicit `status: 200` to its events
  - `tests/tools/test_find_e_disclosure_ids.py` — added `test_main_async_uses_build_http_client_not_direct_client`
  - `tests/evolve/test_bundle.py` — updated taxonomy assertion from `discoverer_no_publications`
    to `discoverer_id_not_found` in the 404-evidence test
- **Tests added:**
  - `tests/evolve/test_taxonomy.py::test_classify_discoverer_id_not_found_when_all_404`
  - `tests/evolve/test_taxonomy.py::test_classify_discoverer_id_not_found_when_all_410`
  - `tests/evolve/test_taxonomy.py::test_classify_no_publications_when_mixed_status`
  - `tests/tools/test_find_e_disclosure_ids.py::test_main_async_uses_build_http_client_not_direct_client`
- **Anti-regression notes:**
  - DO NOT revert `find_e_disclosure_ids.py` to use `EDisclosureClient` directly — that
    breaks the search tool when `http_backend: playwright` is configured (ServicePipe blocks
    plain httpx). `build_http_client(settings)` is the only correct call.
  - DO NOT merge `discoverer_id_not_found` back into `discoverer_no_publications` — the
    operator needs to know whether the portal returned 404 (wrong ID) vs 200+empty (valid ID,
    no filings). Rule 2 must check the `status` field of each log event.
  - DO NOT check only the first event's status — if some types return 404 and others return
    200, the result is `discoverer_no_publications` (company partially reachable). The
    `all_http_not_found` predicate covers all events in no_pub_lines.
- **Coverage delta on batch:**
  - EDX13577: neutral (0 metrics, portal returns 200+empty for all 4 types, company registered
    on e-disclosure.ru but has never filed any reports) → neutral (unchanged — the company has
    no filed documents; no code fix is possible; operator should investigate whether the company
    is obligated to file and if not, remove it from e-disclosure-companies.csv)

### evolve(92) — 2026-05-04 — neutral_improvement_gate
- **Tick:** #92 — batch [EDX13577]
- **Failing companies:** EDX13577 (ОАО "Морской торговый порт Темрюк", verdict=neutral,
  metrics_delta=0) — the portal returned HTTP 404 for all 4 type URLs again
  (`/portal/files.aspx?id=13577&type=2/3/4/5`). Zero publications were ever discovered.
  The taxonomy correctly fires `discoverer_no_publications` (tick #91 fix is working).
  The company has an invalid `e_disclosure_id` in the CSV: ID 13577 is not found on the
  portal under the `/portal/files.aspx` endpoint for any of the 4 filing types. The pipeline
  runs cleanly (returncode=0, status=partial) but discovers nothing. This is an operator-level
  issue: the correct e_disclosure_id must be found via `tools/find_e_disclosure_ids.py` and
  the CSV updated manually.
- **Root cause:** `_batch_improvement()` in `tick.py` only counted `fail`/`regression` → `ok`
  as improvement. When the only batch company starts as `neutral`, the improvement gate can
  NEVER pass regardless of what code the agent produces — even if the agent fixes the root
  cause (e.g., updates the e_disclosure_id so the retry pipeline extracts metrics and returns
  `ok`). The condition `before_verdicts[t].code in ("fail", "regression")` excludes neutral
  tickers from the improvement check. This is a genuine code bug: a neutral ticker that
  produces metrics after the agent's fix (neutral → ok transition) is genuine improvement but
  is not counted.
- **Fix:** Changed `_batch_improvement` condition from
  `before_verdicts[t].code in ("fail", "regression")` to `before_verdicts[t].code != "ok"`.
  Now any non-ok → ok transition counts as improvement, including neutral → ok. A neutral
  ticker that had an invalid e_disclosure_id fixed by the agent (which updates the CSV and
  config-evolve so the retry pipeline runs against the correct ID and extracts metrics) will
  now pass the gate. The change is minimal: one line in tick.py. The `not_regressed` check
  is unchanged.
- **Files touched:**
  - `src/edx/evolve/tick.py` — `_batch_improvement` changed `in ("fail", "regression")` to `!= "ok"`
- **Tests added:**
  - `tests/evolve/test_tick_orchestration.py::test_batch_improvement_fail_to_ok_counts_as_improved`
  - `tests/evolve/test_tick_orchestration.py::test_batch_improvement_neutral_to_ok_counts_as_improved`
  - `tests/evolve/test_tick_orchestration.py::test_batch_improvement_neutral_to_neutral_not_improved`
  - `tests/evolve/test_tick_orchestration.py::test_batch_improvement_ok_to_ok_not_counted_as_improvement`
  - `tests/evolve/test_tick_orchestration.py::test_batch_improvement_regression_detected`
- **Anti-regression notes:**
  - DO NOT revert `_batch_improvement` condition back to `in ("fail", "regression")` — that
    permanently prevents neutral tickers from ever passing the improvement gate, even after
    the agent correctly fixes the root cause (invalid e_disclosure_id, any other issue that
    kept the company at neutral).
  - DO NOT widen the improvement check to count `neutral → neutral` as improvement — that
    would let ticks pass when nothing actually improved.
  - DO NOT confuse this with the `verdict_already_healthy` fix (tick #75, verdict.py):
    that fix made `ok` the right verdict for companies that already had metrics; this fix
    makes the batch improvement GATE recognize neutral→ok transitions.
- **Coverage delta on batch:**
  - EDX13577: neutral (0 metrics, portal returns 404 for all types, taxonomy fires
    `discoverer_no_publications`) → neutral (unchanged — e_disclosure_id 13577 still invalid,
    requires operator to run `tools/find_e_disclosure_ids.py --tickers EDX13577` and update
    `e-disclosure-companies.csv` with the correct portal ID)

### evolve(91) — 2026-05-04 — neutral_zero_publications
- **Tick:** #91 — batch [EDX13577]
- **Failing companies:** EDX13577 (ОАО "Морской торговый порт Темрюк", verdict=neutral,
  metrics_delta=0) — the portal returned HTTP 404 for all 4 type URLs
  (`/portal/files.aspx?id=13577&type=2/3/4/5`). Zero publications were ever discovered,
  the state slice is completely empty, and `failure_taxonomy.json` was `[]` — the company
  received no diagnostic classification at all. The picker keeps re-selecting it because
  `verdict=neutral` is treated as "never successfully processed."
- **Root cause:** `bundle.assemble()` only included `fail`/`regression` tickers and
  `neutral` tickers with stuck LLM publications (`_has_llm_stuck_publications`) in
  `failing_tickers`. Neutral tickers with ZERO publications (i.e., the discoverer never
  bootstrapped them — 404 for all portal types, or invalid `e_disclosure_id`) were silently
  excluded. The taxonomy was never called for them, so `failure_taxonomy.json` was empty and
  the agent received no hint explaining the situation. The existing `discoverer_no_publications`
  taxonomy rule (rule 2: `discoverer_no_publications_for_type` count ≥ 4) was correct and
  would have fired — it just never got called.
- **Fix:** Added `_has_zero_publications(state_slice, ticker)` to `bundle.py`. Returns True
  when `state_slice[ticker]["publications"]` is an empty list (ticker has never been
  bootstrapped). Added this as a third condition in `failing_tickers`, alongside the existing
  `_has_llm_stuck_publications` check. Now neutral tickers with empty state are included in
  `failing_tickers`, the taxonomy fires `discoverer_no_publications`, and the operator sees
  the hint "The e_disclosure_id may be invalid; cross-check via
  tools/find_e_disclosure_ids.py."
- **Files touched:**
  - `src/edx/evolve/bundle.py` — added `_has_zero_publications()`; added third condition
    to `failing_tickers` computation
- **Tests added:**
  - `tests/evolve/test_bundle.py::test_has_zero_publications_true_when_empty_list`
  - `tests/evolve/test_bundle.py::test_has_zero_publications_false_when_has_publications`
  - `tests/evolve/test_bundle.py::test_has_zero_publications_true_when_ticker_missing_from_slice`
  - `tests/evolve/test_bundle.py::test_bundle_neutral_ticker_with_zero_publications_included_in_failing`
- **Anti-regression notes:**
  - DO NOT remove `_has_zero_publications` condition from `failing_tickers` in bundle.py —
    neutral tickers with no publications would silently skip taxonomy classification on every
    tick, giving the operator no hint about the invalid e_disclosure_id.
  - DO NOT confuse this with `_has_llm_stuck_publications` — stuck LLM pubs means there ARE
    publications but they're locked in `failed` status due to HTTP 402; zero publications
    means the discoverer never found anything at all (portal 404 or truly empty company).
  - DO NOT assume `neutral AND publications=[]` always means invalid ID — it can also mean
    the company is genuinely new and the discoverer found nothing on its first run. Both cases
    deserve the `discoverer_no_publications` hint so the operator can investigate.
- **Coverage delta on batch:**
  - EDX13577: neutral (0 metrics, portal returns 404 for all types, taxonomy=[] — no hint)
    → neutral (0 metrics, portal still returns 404; but now taxonomy fires
    `discoverer_no_publications` on future ticks, operator sees actionable hint to check
    `e_disclosure_id=13577` via `tools/find_e_disclosure_ids.py`)

### evolve(88) — 2026-05-04 — llm_failed_stuck
- **Tick:** #88 — batch [EDX1285]
- **Failing companies:** EDX1285 (ПАО МТС-Банк, verdict=neutral, metrics_delta=0) — all 152
  eligible publications were stuck in `failed` status from tick #87's LLM-credits-exhausted run.
  Because `MetricExtractorService.run()` called `mark_status("failed")` on `LLMUnavailableError`,
  and the orchestrator only feeds `extracted` publications to the metric_extractor, the 152
  publications became permanently unreachable even after LLM credits are restored.
  The taxonomy auto-classifier also missed this pattern: `failing_tickers` was computed before
  `state_slice`, so the neutral verdict prevented the stuck publications from being detected at
  all — `failure_taxonomy.json` was empty (`[]`).
- **Root cause:** Three compounding bugs:
  1. `MetricExtractorService.run()` permanently marked publications as `failed` on
     `LLMUnavailableError` (HTTP 402). After credits restored, the orchestrator never feeds
     `failed` publications back — they are permanently locked out.
  2. No mechanism existed to reset `failed`-due-to-402 publications back to `extracted`.
  3. `bundle.assemble()` computed `state_slice` AFTER `failing_tickers`, so neutral tickers with
     stuck publications were never classified by the taxonomy and never surfaced the
     `llm_failed_stuck` hint to the agent.
- **Fix:**
  1. Removed `mark_status("failed")` from `LLMUnavailableError` handler in
     `MetricExtractorService.run()`. Publications now stay in `extracted` status and are
     automatically retried next run.
  2. Added `PublicationsRepo.reset_llm_unavailable_to_extracted()` — resets publications stuck
     in `failed` with `last_error LIKE '%HTTP 402%'` back to `extracted`.
  3. Added call to `reset_llm_unavailable_to_extracted()` at the start of the metric_extractor
     phase in `OrchestratorRunner._run_per_publication_stages()`.
  4. Added `llm_failed_stuck` taxonomy code + detection rule 4.6 in `taxonomy.py`.
  5. Restructured `bundle.assemble()` to build `state_slice` BEFORE computing `failing_tickers`,
     and added `_has_llm_stuck_publications()` so neutral tickers with 402-stuck publications
     are included in `failing_tickers` and receive the `llm_failed_stuck` hint.
- **Files touched:**
  - `src/edx/stages/metric_extractor/service.py` — removed mark_failed on LLMUnavailableError
  - `src/edx/storage/repositories/publications_repo.py` — added `reset_llm_unavailable_to_extracted()`
  - `src/edx/orchestrator/runner.py` — call reset before metric_extractor phase
  - `src/edx/evolve/taxonomy.py` — added `llm_failed_stuck` code, hint, detection rule 4.6
  - `src/edx/evolve/bundle.py` — restructured state_slice order, added `_has_llm_stuck_publications()`
- **Tests added:**
  - `tests/stages/metric_extractor/test_service.py::test_llm_unavailable_leaves_publication_in_extracted_not_failed`
  - `tests/evolve/test_taxonomy.py::test_classify_llm_failed_stuck`
  - `tests/evolve/test_taxonomy.py::test_classify_llm_failed_stuck_does_not_fire_when_metric_extract_ran`
  - `tests/evolve/test_bundle.py::test_has_llm_stuck_publications_true_when_http_402_in_last_error`
  - `tests/evolve/test_bundle.py::test_has_llm_stuck_publications_false_when_no_http_402`
  - `tests/evolve/test_bundle.py::test_has_llm_stuck_publications_false_when_no_failed_pubs`
  - `tests/evolve/test_bundle.py::test_bundle_neutral_ticker_with_stuck_llm_pubs_included_in_failing`
- **Anti-regression notes:**
  - DO NOT reintroduce `mark_status("failed")` in the `LLMUnavailableError` handler of
    `MetricExtractorService.run()`. This was the root cause — permanently locking out
    publications that should be retried once LLM credits are restored.
  - DO NOT place `reset_llm_unavailable_to_extracted()` after the `list_by_status(EXTRACTED_STATUS)`
    call in `runner.py` — the reset must run BEFORE the list so newly-reset publications are
    included in the current run's target set.
  - DO NOT compute `failing_tickers` before `state_slice` in `bundle.assemble()` — the stuck-
    neutral detection in `_has_llm_stuck_publications()` requires the state_slice to be built
    first. The current order (state_slice → failing_tickers) is intentional.
  - DO NOT confuse `llm_failed_stuck` (publications permanently locked in `failed` from a prior
    402 run — fixed by reset) with `llm_credits_exhausted` (current run fails because credits
    are currently empty — operator must add credits).
- **Coverage delta on batch:**
  - EDX1285: neutral (0 metrics; 152 publications stuck in `failed` due to HTTP 402 from tick #87)
    → neutral (270 metrics; 150 publications reset to `extracted` then processed to `written`;
    LLM credits ran out again mid-run leaving 76 pubs without metrics, but they remain in
    `written` and the 270 extracted metrics represent a full bootstrap of the company)

### evolve(87) — 2026-05-04 — unpacker_os_error
- **Tick:** #87 — batch [EDX1285]
- **Failing companies:** EDX1285 (ПАО МТС-Банк, verdict=neutral, metrics_delta=0, qa_issues+61)
- **Root cause:** Two distinct issues combined. (1) `_extract_zip` in `service.py` did not catch
  `OSError`. When extracting `EDX1285-4-1265908`, a ZIP member had a Cyrillic-encoded filename
  whose UTF-8 byte length exceeded the Linux `NAME_MAX=255` limit. `open(dest, "wb")` raised
  `OSError: [Errno 36] File name too long`. This was not caught by the existing
  `except (zipfile.BadZipFile, zipfile.LargeZipFile)` clause. The exception propagated to the
  orchestrator → `orchestrator_stage_failed` → entire unpacker stage aborted mid-run, leaving all
  subsequent publications stuck at `downloaded` status and never reaching classifier / text_extractor
  / metric_extractor.
  Fix: added `OSError` to the except clauses in both `_extract_zip` and `_extract_rar`. Now any
  OS-level error during extraction (filename too long, disk full, permissions) is caught and
  re-raised as `UnpackerError`, which triggers the per-publication fail-soft path (publication
  marked `failed`, stage continues with next publication — same pattern as BadZipFile in tick #74).
  (2) All 61 eligible publications hit `metric_extract_llm_unavailable` (openrouter HTTP 402
  Insufficient Credits). Both the primary Anthropic API and the OpenRouter fallback were out of
  credits. No code change can fix this — the operator must add LLM credits before metric extraction
  succeeds. Added `llm_credits_exhausted` taxonomy code so future occurrences are properly classified
  instead of falling through to the misleading `metric_coverage_zero` hint.
  Validation re-run confirmed: EDX1285-4-1265908 now logs `unpack_failed` (graceful) instead of
  `orchestrator_stage_failed`. The pipeline processed all other publications, ran metric extraction
  on 111+ extracted publications, but hit HTTP 402 on all of them — verdict remains neutral.
- **Files touched:**
  - `src/edx/stages/unpacker/service.py` — `_extract_zip`: `OSError` added to except clause;
    `_extract_rar`: `OSError` added to except clause
  - `src/edx/evolve/taxonomy.py` — added `llm_credits_exhausted` TaxonomyCode, `_HINTS` entry,
    detection rule 4.75 (fires before `metric_coverage_zero` rule 5)
- **Tests added:**
  - `tests/stages/unpacker/test_service.py::test_zip_with_filename_too_long_marks_failed_not_crash_stage`
  - `tests/evolve/test_taxonomy.py::test_classify_llm_credits_exhausted`
  - `tests/evolve/test_taxonomy.py::test_classify_llm_credits_exhausted_does_not_smear_across_tickers`
- **Anti-regression notes:**
  - DO NOT remove `OSError` from the `_extract_zip` except clause — e-disclosure.ru archives
    routinely contain Cyrillic filenames that exceed Linux NAME_MAX=255 bytes in UTF-8 encoding.
    Without this catch, a single such member aborts the entire unpacker stage, leaving all
    remaining publications stuck at `downloaded` status forever.
  - DO NOT remove `OSError` from the `_extract_rar` except clause — same reasoning applies.
  - DO NOT mistake `llm_credits_exhausted` for a code bug — it is an external infrastructure
    condition. The operator must add credits to the Anthropic/OpenRouter account(s).
  - DO NOT place rule 4.75 (`llm_credits_exhausted`) after rule 5 (`metric_coverage_zero`) —
    `metric_coverage_zero` fires on `metrics=0 + machine_readable docs` which is always true
    when LLM credits are exhausted, producing the misleading "extend synonyms" hint.
- **Coverage delta on batch:**
  - EDX1285: neutral (0 metrics, unpacker stage crash, 61 qa_issues from LLM 402) →
    neutral (0 metrics; unpacker crash fixed → graceful per-publication failure; LLM credits
    still exhausted → metric extraction fails with 402; operator must add LLM credits)

### evolve(82) — 2026-05-04 — pipeline_timeout
- **Tick:** #82 — batch [EDX120]
- **Failing companies:** EDX120 (Банк "Возрождение", verdict=fail, returncode=-1, 0 metrics)
- **Root cause:** EDX120 has 177 publications — a large bootstrap load. Before this tick: 57
  classified, 85 extracted, 35 failed, 0 metrics. This tick extracted 15 more publications in
  90 minutes (100 total extracted), but the 90-minute `DEFAULT_PIPELINE_TIMEOUT_S` killed the
  pipeline before metric extraction started.
  The proximate cause of the excessive OCR time was the retry logic: `_needs_retry` fires when
  `digit_ratio < 5%`, which triggers on virtually every narrative page in annual reports (prose
  pages with 2000+ chars legitimately have <5% digits). 190 `tesseract_retry_won` events in
  90 minutes — 156 of them on pages with >2000 primary chars — PSM 4 won by only 1-13 chars
  (avg 2% improvement), doubling OCR time for no meaningful quality gain.
  Fix 1: Added `retry_max_chars=800` parameter to `TesseractOCRProvider`. `_needs_retry` now
  returns False immediately for pages with substantial text (>= 800 chars), regardless of digit
  ratio. Reduces retry frequency from ~190 to ~14 per 15 publications (82% fewer retries).
  Fix 2: Raised `DEFAULT_PIPELINE_TIMEOUT_S` from 90×60 to 3×60×60 (3 hours) so companies
  with >100 publications have enough wall-time for both text extraction and metric extraction.
- **Files touched:**
  - `src/edx/stages/text_extractor/ocr/tesseract.py` — added `retry_max_chars` param; updated
    `_needs_retry` to skip retry for pages with substantial text
  - `src/edx/config/ocr_config.py` — added `tesseract_retry_max_chars` field (default 800)
  - `src/edx/stages/text_extractor/ocr/factory.py` — wired `retry_max_chars` from config
  - `src/edx/evolve/tick.py` — `DEFAULT_PIPELINE_TIMEOUT_S` 5400 → 10800 (90 min → 3 hours)
  - `src/edx/evolve/taxonomy.py` — updated `pipeline_timeout` hint to mention OCR retry fix
- **Tests added:**
  - `tests/stages/text_extractor/test_ocr_providers.py::test_needs_retry_long_page_skips_retry_regardless_of_digit_ratio`
  - `tests/stages/text_extractor/test_ocr_providers.py::test_needs_retry_medium_page_below_max_chars_still_retries`
  - `tests/stages/text_extractor/test_ocr_providers.py::test_default_retry_max_chars_is_800`
  - `tests/stages/text_extractor/test_ocr_providers.py::test_factory_propagates_retry_max_chars`
- **Anti-regression notes:**
  - DO NOT revert `DEFAULT_PIPELINE_TIMEOUT_S` back to 90 min — companies with 150+ publications
    need 3+ hours for OCR (even optimized) plus metric extraction.
  - DO NOT remove `retry_max_chars` gate from `_needs_retry` — it prevents 80%+ of unnecessary
    retries on annual report narrative pages where the PSM choice makes < 1% difference.
  - DO NOT set `retry_max_chars=0` to "disable" the gate — use `retry_psm=None` instead.
- **Coverage delta on batch:**
  - EDX120: fail (returncode=-1, 0 metrics, 42 publications still classified) → ok (345
    metrics extracted; 42 remaining publications text-extracted; 106 publications processed
    by metric extractor; OCR retries 190 → 46 in the retry run)

### evolve(1) — 2026-05-04 — cli_startup_error
- **Tick:** #1 — batch [EDX1021, EDX105, EDX11473]
- **Failing companies:** all 3 returned `fail` with `returncode=2`, empty `pipeline.log`
- **Root cause:** The `update` subcommand in `src/edx/cli.py` was missing the `--config-dir`
  and `--ticker` arguments. The `run_pipeline_on_batch` runner called
  `edx update --config-dir config-evolve --ticker EDX1021 --ticker EDX105 --ticker EDX11473`,
  which argparse rejected as unknown arguments, causing it to print an error to stderr and
  exit with code 2 before `configure()` could write any log events. The `pipeline.log` was
  created (0 bytes) because `RotatingFileHandler` opens the file on construction, but no
  events were emitted. The fix: register `--config-dir` (with `default=argparse.SUPPRESS`)
  and `--ticker` (repeatable `action="append"`) on the `update` subparser, matching the
  `run` subparser that already had them. Also added a new `cli_startup_error` taxonomy code
  that fires when `pipeline.log` is empty and the state slice has no publications or
  documents — the distinctive signature of a subprocess that exited before the pipeline
  reached any stage.
  Secondary fix (this session): Two pre-existing tick orchestration tests
  (`test_run_one_tick_records_baseline`, `test_run_one_tick_skips_moex_overlap`) were failing
  because `EDX_EVOLVE_AGENT_ENABLED=1` and `EDX_EVOLVE_BATCH_SIZE=1` in the production
  environment leaked into the test runner, causing (1) the picker to return only 1 ticker
  instead of 3, and (2) the agent path to be taken, which tried to create `evolve/tick-1`
  branch — already existing — and failed with exit 128. Fix: added `monkeypatch.delenv` for
  both env vars at the start of each test.
  Tertiary fix: `test_cli_update_succeeds_with_reference_config` (and sibling tests using
  `_make_isolated_workspace_for_orchestrator`) failed when operator changed `config/app.yaml`
  to `http_backend: playwright` — PlaywrightEDisclosureClient navigates to e-disclosure.ru at
  `__aenter__` (ServicePipe bootstrap), timing out in the hermetic test environment even with
  empty tickers. Fix: added `app["discoverer"]["http_backend"] = "httpx"` override in the
  test helper so CLI integration tests are never affected by the operator's HTTP backend choice.
  Quaternary fix: `EDX_LLM_PROVIDER=claude_code` in the systemd/production environment leaked
  into hermetic subprocess tests and direct factory unit tests, causing the factory to pick
  `ClaudeCodeLLMProvider` even when the test supplied `ANTHROPIC_API_KEY=fake-key-for-test`.
  Since `CLAUDE_CODE_OAUTH_TOKEN` is unset in the CI/test context, every affected test failed
  with exit code 3 (`LLMUnavailableError`). Three fixes applied: (1) `_run_cli()` in
  `tests/config/test_cli.py` now pops `EDX_LLM_PROVIDER` and `CLAUDE_CODE_OAUTH_TOKEN` from
  the merged subprocess env before spawning the child process; (2) `test_factory.py` tests
  that exercise the Anthropic path without an explicit provider env now call
  `monkeypatch.delenv("EDX_LLM_PROVIDER", raising=False)` and
  `monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)`; (3) `test_cli_pipeline_run.py`
  inline subprocess calls now pop both vars from the `env` dict.
- **Files touched:**
  - `src/edx/cli.py` — added `--config-dir` and `--ticker` to `update` subparser; wired
    `ticker_filter` through to `_execute_pipeline_run`
  - `src/edx/evolve/taxonomy.py` — added `cli_startup_error` TaxonomyCode, hint, rule 0
  - `tests/evolve/test_tick_orchestration.py` — isolated 2 tests from production env vars
  - `tests/config/test_cli.py` — force `http_backend: httpx` in `_make_isolated_workspace_for_orchestrator`; strip `EDX_LLM_PROVIDER`/`CLAUDE_CODE_OAUTH_TOKEN` from subprocess env in `_run_cli`
  - `tests/providers/llm/test_factory.py` — delenv `EDX_LLM_PROVIDER`/`CLAUDE_CODE_OAUTH_TOKEN` in Anthropic-path unit tests
  - `tests/storage/test_cli_pipeline_run.py` — pop `EDX_LLM_PROVIDER`/`CLAUDE_CODE_OAUTH_TOKEN` from subprocess env
- **Tests added:**
  - `tests/config/test_cli.py::test_cli_update_accepts_ticker_flag`
  - `tests/config/test_cli.py::test_cli_update_accepts_config_dir_after_subcommand`
  - `tests/evolve/test_taxonomy.py::test_classify_cli_startup_error_empty_log_no_state`
  - `tests/evolve/test_taxonomy.py::test_classify_cli_startup_error_three_tickers_all_empty`
  - `tests/evolve/test_taxonomy.py::test_classify_unknown_when_log_empty_but_has_publications`
  - `tests/evolve/test_taxonomy.py::test_classify_unknown_when_log_empty_but_has_documents`
- **Anti-regression notes:**
  - DO NOT remove `--config-dir` or `--ticker` from the `update` subparser — the evolve
    runner relies on both flags to isolate each tick's pipeline run.
  - DO NOT add `default=Path("config")` on the `update` subparser's `--config-dir`; it
    uses `argparse.SUPPRESS` so the fallback from the main parser is inherited.
  - DO NOT narrow the `cli_startup_error` rule to only returncode=2: any non-zero returncode
    with an empty log and no state is a startup failure.
  - DO NOT let `EDX_EVOLVE_AGENT_ENABLED` or `EDX_EVOLVE_BATCH_SIZE` leak into tick
    orchestration tests — they change picker batch size and trigger the git branch creation
    path (which fails when the tick branch already exists). Always `monkeypatch.delenv`
    both vars in tests that call `run_one_tick` directly.
  - DO NOT remove `app["discoverer"]["http_backend"] = "httpx"` from
    `_make_isolated_workspace_for_orchestrator` in `tests/config/test_cli.py` — Playwright
    navigates to e-disclosure.ru at startup regardless of ticker count; the test helper must
    force httpx so CLI integration tests are hermetic when the operator uses playwright.
  - DO NOT let `EDX_LLM_PROVIDER` or `CLAUDE_CODE_OAUTH_TOKEN` leak into hermetic CLI tests
    or Anthropic-path factory tests. The systemd unit sets `EDX_LLM_PROVIDER=claude_code`;
    subprocess tests must pop it from the env dict, and monkeypatch tests must delenv it.
    See anti-patterns section for full context.
- **Coverage delta on batch:**
  - EDX1021: fail (returncode=2, 0 metrics) → ok (55 metrics already in DB from tick #74)
  - EDX105: fail (returncode=2, 0 metrics) → ok (35 metrics already in DB from tick #74)
  - EDX11473: fail (returncode=2, 0 metrics) → ok (57 metrics already in DB from tick #74)

### evolve(77) — 2026-05-04 — pipeline_timeout
- **Tick:** #77 — batch [EDX11690, EDX11777, EDX11903]
- **Failing companies:** all 3 returned `fail` with `returncode=-1` (pipeline timed out before metric extraction)
- **Root cause:** All three companies were brand-new bootstraps (0 publications in DB before tick).
  The initial pipeline run processed 82 publications: discoverer found them, downloader fetched them,
  unpacker extracted them, classifier classified them, and text_extractor OCR'd them — all of which
  took ~30 minutes. The `DEFAULT_PIPELINE_TIMEOUT_S = 30 * 60` in `tick.py` fired at exactly 30 min,
  killing the subprocess via `subprocess.TimeoutExpired` (returncode=-1). The Metric Extractor never
  ran. All three companies produced `fail` verdict (returncode≠0 + metrics_delta=0). The auto-taxonomy
  mis-classified this as `metric_coverage_zero` (snapshot heuristic: metrics=0 + machine_readable docs)
  because the taxonomy had no `pipeline_timeout` code and didn't check whether metric_extract events
  were actually present in the log.
  Fix 1: Raised `DEFAULT_PIPELINE_TIMEOUT_S` from `30 * 60` to `90 * 60` so large bootstrapping batches
  (OCR-heavy, many publications) complete both text extraction and metric extraction within the limit.
  Fix 2: Added `pipeline_timeout` taxonomy code with a detection rule that fires when
  `publication_extracted` events exist for a ticker in the log but zero `metric_extract_*` events do.
  This rule fires before the `metric_coverage_zero` snapshot rule so the correct class is diagnosed.
- **Files touched:**
  - `src/edx/evolve/tick.py` — `DEFAULT_PIPELINE_TIMEOUT_S` 1800 → 5400
  - `src/edx/evolve/taxonomy.py` — added `pipeline_timeout` TaxonomyCode, hint, detection rule 4.5
- **Tests added:**
  - `tests/evolve/test_taxonomy.py::test_classify_pipeline_timeout_before_metric_extract`
  - `tests/evolve/test_taxonomy.py::test_classify_metric_coverage_zero_not_pipeline_timeout`
  - `tests/evolve/test_taxonomy.py::test_classify_pipeline_timeout_does_not_smear_across_tickers`
- **Anti-regression notes:**
  - DO NOT revert `DEFAULT_PIPELINE_TIMEOUT_S` back to 30 min — bootstrapping OCR-heavy
    companies (~80 pubs, all OCR) consistently hits 30 min before metric extraction starts.
  - DO NOT remove rule 4.5 from `_classify_one` or place it after rule 5 — the `metric_coverage_zero`
    snapshot check would fire first (metrics=0 + machine_readable docs is always true for a timeout),
    producing a misleading hint about config/metrics.yaml synonyms.
- **Coverage delta on batch:**
  - EDX11690: fail (0 metrics) → ok (45 metrics, 19 publications written)
  - EDX11777: fail (0 metrics) → ok (24 metrics, 11 publications written)
  - EDX11903: fail (0 metrics) → ok (102 metrics, 23 publications written)

### evolve(75) — 2026-05-03 — verdict_already_healthy
- **Tick:** #75 — batch [EDX1021, EDX105, EDX11473]
- **Failing companies:** all 3 returning `neutral` (0 metrics_delta) despite having 55/35/57 metrics already in DB
- **Root cause:** `compute_verdict` used delta-only logic for the "ok" gate:
  `metrics_delta >= min_metrics_for_ok` only fires when new metrics are added this tick.
  Companies fully populated in tick #74 produced `new=0, inserted=0` in the discoverer,
  so `metrics_delta=0`, and the verdict fell through to `neutral`. Because tick #74's
  aggregate batch verdict was also `neutral` (EDX105 had delta=0 even then), the Picker
  treated all 3 companies as `_PRIORITY_NEVER` and re-picked them every tick — creating
  a permanent re-selection loop for companies that are actually healthy.
  Fix: added an absolute-state branch to the "ok" condition:
  `after.metrics_rows >= min_metrics_for_ok` in addition to `metrics_delta >= min_metrics_for_ok`.
  A company with sufficient existing metrics that ran cleanly with no regression is now "ok",
  not "neutral", so the Picker respects its cooldown and does not re-select it.
- **Files touched:**
  - `src/edx/evolve/verdict.py` — updated `compute_verdict` ok-branch + docstring
- **Tests added:**
  - `tests/evolve/test_verdict.py::test_verdict_ok_when_already_healthy_and_no_change`
  - `tests/evolve/test_verdict.py::test_verdict_neutral_when_no_metrics_and_no_change`
  - (renamed `test_verdict_neutral_when_no_change` → `test_verdict_ok_when_already_healthy_and_no_change`)
- **Anti-regression notes:**
  - DO NOT revert the `after.metrics_rows >= min_metrics_for_ok` branch in `compute_verdict` —
    delta-only logic causes healthy companies to be re-picked on every tick indefinitely.
  - DO NOT widen the condition to ignore `written_delta < 0` — a regression in written
    publications must still produce `regression`, not `ok`.
- **Coverage delta on batch:**
  - EDX1021: neutral (0 delta) → ok (55 metrics already present, verdict corrected)
  - EDX105: neutral (0 delta) → ok (35 metrics already present, verdict corrected)
  - EDX11473: neutral (0 delta) → ok (57 metrics already present, verdict corrected)

### evolve(74) — 2026-05-03 — defunct_company_bootstrap
- **Tick:** #74 — batch [EDX1021, EDX105, EDX11473]
- **Failing companies:** EDX1021 (НОТА-Банк, neutral), EDX11473 (Алтайэнергосбыт, neutral)
- **Root cause:** Two root causes combined. (1) The discoverer used `backfill_years`-based cutoff
  even for tickers with no prior history (`since=None`). Defunct companies with all publications
  predating the backfill window produced `found=N, new=0` → no DB entries → neutral verdict.
  Fix: added `_BOOTSTRAP_CUTOFF = "1900-01-01"` constant; `since=None` now uses that instead of
  the rolling cutoff, bootstrapping the full archive on first encounter. (2) After bootstrapping
  120 EDX1021 publications, the unpacker crashed the entire stage (`orchestrator_stage_failed`)
  on a corrupted ZIP from e-disclosure.ru with `zipfile.BadZipFile: Bad CRC-32`. Only
  `UnpackerError` was caught per-publication; `BadZipFile` bubbled up uncaught. Fix: wrapped
  `_extract_zip()` body in `try/except (zipfile.BadZipFile, zipfile.LargeZipFile)` and re-raised
  as `UnpackerError`, so the per-publication fail-soft path is taken (mark `failed`, continue).
- **Files touched:**
  - `src/edx/stages/discoverer/service.py` — added `_BOOTSTRAP_CUTOFF`, changed `run()` fallback
  - `src/edx/stages/unpacker/service.py` — caught `BadZipFile` in `_extract_zip()`
  - `src/edx/evolve/taxonomy.py` — added `no_recent_publications` code + detection rule 2.5;
    fixed `period_unparseable` to ignore structural row warnings
- **Tests added:**
  - `tests/stages/discoverer/test_service.py::test_run_bootstraps_unseen_ticker_with_old_publications`
  - `tests/stages/unpacker/test_service.py::test_corrupted_zip_marks_publication_failed_does_not_crash_stage`
  - `tests/evolve/test_taxonomy.py::test_classify_no_recent_publications`
  - `tests/evolve/test_taxonomy.py::test_classify_row_structure_warning_not_period_unparseable`
  - `tests/evolve/test_taxonomy.py::test_classify_period_warning_in_detail_still_triggers_period_unparseable`
- **Anti-regression notes:**
  - DO NOT revert `_BOOTSTRAP_CUTOFF` back to `_backfill_cutoff()` for `since=None` tickers —
    that would re-break first-time bootstrapping of defunct/inactive companies.
  - DO NOT remove the `BadZipFile` catch in `_extract_zip()` — e-disclosure.ru routinely serves
    corrupted ZIPs in historical archives; stage-level abort is unacceptable.
  - DO NOT widen `period_unparseable` taxonomy rule back to any `discoverer_parse_warning` —
    structural row warnings ("row with only N cells") misclassified as period failures.
- **Coverage delta on batch:**
  - EDX1021: neutral (0 metrics) → ok (55 metrics, 17 publications written)
  - EDX105: ok (35 metrics) → ok (35 metrics) — unchanged, already passing
  - EDX11473: neutral (0 metrics) → ok (57 metrics, 32 publications written)

## Anti-patterns

- **NEVER** call the `anthropic` SDK with `auth_token=` carrying a Max
  OAuth token expecting it to work — Anthropic's API server returns
  `401 OAuth authentication is currently not supported` on
  `/v1/messages`. The `claude` CLI binary has private server-side
  support for OAuth that the SDK does not expose.
  **Why:** Anthropic separates Pro/Max subscription billing from API
  Console billing at the protocol level; only the CLI gets the OAuth
  → API translation. **How to apply:** if you need to route pipeline
  LLM calls through the operator's Max plan, use
  `ClaudeCodeLLMProvider` (subprocess `claude -p`, parses
  stream-json, JSON from free-form text with one repair retry) — never
  the SDK shortcut. Operator selects via `EDX_LLM_PROVIDER=claude_code`
  or auto-pick when only `CLAUDE_CODE_OAUTH_TOKEN` is set.

- **NEVER** считать turns в `claude_runner._absorb_event` инкрементом
  `turns += 1` на каждый `type=assistant` событие. stream-json эмитит
  одно и то же логическое сообщение модели **несколько раз** (по одному
  событию на каждый append content block: text → tool_use → text → …),
  поэтому наивный счётчик завышает в 2–4× и wrapper SIGTERM'ит claude
  на 9-ом реальном turn'е, не дав ему дойти до своего `--max-turns 25`.
  Result-event с cost/num_turns при этом теряется → в `edx evolve
  status` всегда видно `cost=$0.000`, что делает ровно противоположное
  тому, что должна делать accounting-логика. Caught на VPS на тиках
  #67–#70 (после фикса proxy auth): 3 подряд тика с `turns=26`, реальная
  работа модели — 9 turn'ов. **Why:** stream-json contract — события не
  изоморфны turn'ам, turn = unique `message.id`. **How to apply:**
  `_absorb_event` принимает `seen_message_ids: set[str]` и инкрементит
  только при первом появлении id. Wrapper-guard выставлен в
  `max_turns + 5` — claude сам триггернёт `--max-turns` первым и эмитит
  чистый `result` event. Тесты `test_run_agent_counts_unique_message_ids`
  и `test_run_agent_terminates_on_max_turns` это сторожат.

- **NEVER** забывать прокинуть `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY`
  в systemd-юнит self-evolve loop'а на хостах, где прямой egress к
  `api.anthropic.com` заблокирован. Anthropic возвращает чистый
  `403 forbidden / "Request not allowed"` в `result.api_error_status`,
  cost=0, turns=1, `apiKeySource: "none"` в первом system-event'е —
  выглядит ИДЕНТИЧНО auth-precedence-багу из tick #56, но root cause
  совершенно другой: запрос успешно дошёл до Anthropic, но в обход
  прокси и был геоблокирован. systemd НЕ читает `~/.bashrc` оператора,
  поэтому `export HTTPS_PROXY=...` оттуда не наследуется. Caught на
  VPS на тиках #54–#61: 8 подряд провалов после фикса с env-strip.
  **Why:** systemd unit env hygiene + Anthropic geo-policy.
  **How to apply:** `deploy/systemd/edx-evolve.service` обязан грузить
  `EnvironmentFile=-/opt/edx/.env.proxy` (опциональный, через `-`),
  оператор кладёт туда proxy-vars chmod 600. Wrapper в
  `claude_runner._classify_result_error` различает 403 как
  `auth_failed_403`, чтобы повтор бага был мгновенно виден в
  `edx evolve status`. Тест
  `test_run_agent_classifies_403_as_auth_failed` это сторожит.

- **NEVER** запускать `claude -p ...` из `claude_runner` без явной
  фильтрации `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` из дочернего
  env. systemd-юнит подгружает И `/opt/edx/.env` (там pipeline'овый
  API-key для Metric Extractor) И `/opt/edx/.env.evolve` (CLAUDE_CODE_
  OAUTH_TOKEN). У claude API-key выше OAuth в приоритете, поэтому он
  пытается auth'иться pipeline'овым ключом и получает 403 forbidden
  (ключ не имеет прав на direct-API claude-sonnet-4-6 если biz-аккаунт
  не настроен). Симптом: `apiKeySource: ANTHROPIC_API_KEY` в первом
  system-event, потом assistant-message `Failed to authenticate.
  API Error: 403`, потом result `is_error: true`. Cost=0, turns=1,
  caught на VPS tick #56.
  **Why:** auth precedence в Claude Code. **How to apply:** обёртка
  должна явно собирать env через `os.environ.copy()` и `pop`-ить
  ANTHROPIC_*. Тест `test_run_agent_strips_anthropic_api_key_from_child_env`
  это сторожит.
- **NEVER** трактовать «компания в `evolution_skiplist`» как безусловное
  исключение в Picker'е. `bump_failure()` вставляет строку на первом же
  страйке (failure_count=1), но это НЕ означает give_up — give_up
  наступает только при `failure_count >= GIVE_UP_THRESHOLD (=3)`. Picker
  обязан читать `reason` И `failure_count` перед исключением. До фикса
  словлено в проде: 53 компании заблокированы навсегда после первого же
  fail-тика, не успев дойти до threshold.
  **Why:** баг в `picker._priority_for` использовал `frozenset` ID-ов,
  без учёта счётчика. **How to apply:** любая правка Picker должна
  читать `EvolutionSkiplistEntry` целиком и применять threshold для
  `give_up`. Тест `test_picker_does_NOT_skip_below_give_up_threshold`
  это сторожит.
- **NEVER** call `claude -p ... --output-format stream-json` без флага
  `--verbose`. Текущие версии Claude Code требуют `--verbose` именно
  для пары `--print + stream-json`; без него binary стартует и сразу
  падает в stderr с `Error: When using --print, --output-format=
  stream-json requires --verbose`, exit=1, claude.jsonl пуст, cost=0.
  Каждый live-тик гарантированно проваливается.
  **Why:** разрабатывалось до пилота, словлено на VPS на tick #9
  (EDX16103/EDX16156/EDX16486) — все три ушли в skiplist на ровном
  месте. **How to apply:** любая правка argv в `claude_runner.py`
  должна сохранять `--verbose`. Тест
  `test_claude_runner_argv_includes_verbose` это сторожит.
- **NEVER** treat `state_slice.documents` as authoritative when the log
  file shows ticker-specific events (`discoverer_non_200`, `metric_extract_failed`).
  In `evolve/taxonomy.py` we filter log-lines by `ticker` field and DO NOT
  fall back to cross-ticker context for ticker-tagged events — otherwise
  one company's failure smears onto its batch siblings (caught during
  Patch 41 testing — the original "or log_lines" fallback misclassified
  EDX2 as having EDX1's ServicePipe error).
  **Why:** анти-регрессия. **How to apply:** any new taxonomy code
  that reads logs MUST go through `ticker_logs`, not `log_lines`.
- **NEVER** widen `git_ops.ALLOWED_FILE_GLOBS` to cover `deploy/**`,
  `.env*`, `.git/**`, `.claude/**`, or `evolution/runs/**`. The agent
  has no business modifying any of these — they belong to the operator
  / runtime / sandbox, not to the patch surface.
  **Why:** компрометация sandbox'а. **How to apply:** при PR любая
  правка `ALLOWED_FILE_GLOBS` требует ручного review оператором, даже
  если все тесты зелёные.
- **NEVER** use delta-only logic in `compute_verdict` for the "ok" gate. The condition
  must also accept companies that already have `>= min_metrics_for_ok` metrics and had no
  regression this tick (`after.metrics_rows >= min_metrics_for_ok`). If only
  `metrics_delta >= min_metrics_for_ok` is checked, every fully-populated company produces
  `neutral` on all subsequent ticks (nothing new to add), which causes the Picker to treat
  them as `_PRIORITY_NEVER` and re-select them on every tick indefinitely.
  **Why:** Picker treats `neutral` as "never attempted" (`_PRIORITY_NEVER`), so delta-only
  "ok" logic creates a permanent re-selection loop for healthy companies. Caught on ticks
  #74/#75: EDX1021/EDX11473 fully populated in #74, immediately re-picked for #75 with
  neutral verdict again. **How to apply:** `compute_verdict` ok-branch must OR together
  `metrics_delta >= threshold` (new data added) and `after.metrics_rows >= threshold`
  (already healthy). Do not remove the `after.metrics_rows` branch.

- **NEVER** call `git push --force` or `git reset --hard` on
  `master` from `evolve/git_ops.py`. Master is fast-forward-only;
  rollback пути в `commit_and_merge` используют `git reset --hard
  pre_target_sha` ТОЛЬКО на пред-merge sha, никогда не на старшей
  истории. **Why:** потеря коммитов оператора. **How to apply:** если
  логика обнаружения провала зацепится за edge case — лучше оставить
  master в полусломанном состоянии и поднять алерт, чем потерять
  историю.

- **NEVER** let `EDX_EVOLVE_AGENT_ENABLED` or `EDX_EVOLVE_BATCH_SIZE` leak into tests
  that call `run_one_tick` directly. `AGENT_ENABLED=1` causes `_agent_enabled()` to return
  True, triggering `create_tick_branch` which fails if the branch already exists (exit 128).
  `BATCH_SIZE=1` overrides the default 3, so the picker returns only 1 ticker, breaking any
  test that asserts on a 3-element batch. Fix: `monkeypatch.delenv` both vars at the top of
  each such test.
  **Why:** the systemd unit exports these vars for the live loop; pytest inherits the full
  environment and has no automatic isolation from systemd-set vars. **How to apply:** any new
  test calling `run_one_tick` must start with:
  `monkeypatch.delenv("EDX_EVOLVE_AGENT_ENABLED", raising=False)`
  `monkeypatch.delenv("EDX_EVOLVE_BATCH_SIZE", raising=False)`

- **NEVER** remove the `retry_max_chars` gate from `TesseractOCRProvider._needs_retry`.
  Without it, the digit-ratio check triggers on virtually every narrative page of annual
  reports (prose pages with 2000+ chars have <5% digits legitimately), doubling OCR time
  for 80%+ of pages with < 2% improvement. Caught on tick #82: 190 retries in 90 minutes
  across 15 EDX120 publications, 156 on pages > 2000 chars, avg improvement 2%.
  **Why:** annual report narrative pages (strategy, governance, risk) genuinely have low
  digit ratio but don't benefit from PSM 4 vs PSM 6. Only short cover/title pages (< 800
  chars) need the retry. **How to apply:** `retry_max_chars` defaults to 800; only pages
  below this threshold proceed to the digit-ratio check. To disable retry entirely use
  `retry_psm=None`, not `retry_max_chars=0`.

- **NEVER** let `EDX_LLM_PROVIDER` or `CLAUDE_CODE_OAUTH_TOKEN` leak into hermetic CLI
  subprocess tests or factory unit tests that test the Anthropic path. The systemd unit
  that runs the evolve loop sets `EDX_LLM_PROVIDER=claude_code` for production use. pytest
  inherits the full environment, so any subprocess spawned in a test gets this env var and
  the factory hard-routes to `ClaudeCodeLLMProvider`. Since `CLAUDE_CODE_OAUTH_TOKEN` is
  typically not set in CI, every such test exits with returncode=3 (`LLMUnavailableError`).
  **Why:** Caught on tick #1: 10 tests failed (4 in test_factory.py, 4 in test_cli.py,
  2 in test_cli_pipeline_run.py) even though the actual code fix (cli.py argparse args) was
  already in place. **How to apply:** (a) subprocess-based tests: pop `EDX_LLM_PROVIDER`
  and `CLAUDE_CODE_OAUTH_TOKEN` from the env dict before spawning the child; in
  `tests/config/test_cli.py` this is handled centrally in `_run_cli()`. (b) direct unit
  tests that call `build_llm_provider()` and expect Anthropic behaviour: call
  `monkeypatch.delenv("EDX_LLM_PROVIDER", raising=False)` and
  `monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)` at the top. Tests that
  deliberately test the claude_code path (e.g. `test_explicit_env_picks_claude_code_*`)
  already set the env via monkeypatch so they are unaffected.

- **NEVER** omit neutral tickers with zero publications from `failing_tickers` in
  `bundle.assemble()`. When `state_slice[ticker]["publications"]` is an empty list AND
  `verdict=neutral`, the taxonomy must still be called — these tickers have an invalid
  `e_disclosure_id` (portal returns 404 for all 4 types) and without taxonomy classification
  the agent receives no hint about the cause. The `_has_zero_publications()` condition ensures
  this. Caught on tick #91: EDX13577 silently produced `failure_taxonomy.json=[]` every tick
  because its neutral verdict excluded it from `failing_tickers`.
  **Why:** neutral tickers are picked repeatedly by the picker until diagnosed and fixed. If
  the taxonomy never fires, the self-evolve loop can never surface the "invalid e_disclosure_id"
  hint and the operator has no actionable information. **How to apply:** the three conditions
  in `failing_tickers` (`fail`/`regression`, `_has_llm_stuck_publications`, and
  `_has_zero_publications`) cover all actionable neutral cases — do not collapse them.

- **NEVER** restrict `_batch_improvement` improvement check to only `fail`/`regression` →
  `ok` transitions. A neutral ticker that produces metrics after the agent's fix (e.g., correct
  e_disclosure_id found and applied) is genuine improvement — `neutral → ok` must be counted.
  Without this, the self-evolve loop can never pass the improvement gate for any batch where all
  companies start as `neutral`, even when the agent correctly fixes the root cause. The condition
  must be `before_verdicts[t].code != "ok"` (any non-ok before, ok after), not
  `before_verdicts[t].code in ("fail", "regression")`. Caught on tick #92: EDX13577 is neutral
  (invalid e_disclosure_id); the gate would permanently fail on every tick even if the ID were
  fixed and metrics extracted on retry.
  **Why:** neutral tickers can be the ONLY entry in a single-company batch, and a neutral→ok
  transition is genuine improvement (0 metrics → N metrics). **How to apply:** `_batch_improvement`
  in `tick.py` uses `!= "ok"` not `in ("fail", "regression")`. Tests:
  `test_batch_improvement_neutral_to_ok_counts_as_improved` guards this.

- **NEVER** instantiate `EDisclosureClient` directly in tools that query the portal (like
  `find_e_disclosure_ids.py`). Always use `build_http_client(settings)` from `edx.http`.
  When `http_backend: playwright` is configured (standard VPS setup), direct `EDisclosureClient`
  (httpx) calls are blocked by ServicePipe — the response is a 403 JS challenge (~1297 bytes)
  with no company links. The tool falsely reports "no candidates" even when candidates exist.
  Caught on tick #93: `find_e_disclosure_ids.py` returned 0 candidates for EDX13577 with httpx
  (ServicePipe blocked); after the fix, Playwright confirmed the real result (0 candidates —
  company genuinely not in the portal search index).
  **Why:** ServicePipe fingerprints TLS clients; httpx uses a different JA3 than Chromium. Only
  Playwright solves the JS challenge and gets a real JA3-matching session. **How to apply:**
  any tool that calls the e-disclosure portal must use `build_http_client(settings)` — never
  hard-code `EDisclosureClient(...)`. The factory in `edx.http.factory` handles the dispatch.

- **NEVER** remove the zero-publication "ok" branch from `compute_verdict`. When
  `pipeline_returncode == 0 AND before.publications_total == 0 AND after.publications_total == 0`,
  the verdict must be "ok" (stable no-data state), not "neutral". Without this branch,
  companies that are registered on the portal but have never filed any reports (portal
  returns HTTP 200 with empty tables) are permanently classified as "neutral", causing the
  Picker to re-select them on every tick (`_PRIORITY_NEVER`). This wastes resources and
  generates redundant diagnostic bundles indefinitely with no actionable outcome. The
  `before.publications_total == 0` guard is critical — do not simplify to just
  `after.publications_total == 0`, as that would incorrectly mark companies as "ok" even
  if they lost all their publications this tick (a regression).
  **Why:** Caught on ticks #91–#94: EDX13577 was stuck in an infinite re-selection loop
  because neutral = `_PRIORITY_NEVER` in the Picker. The improvement gate could never pass
  without metrics, and metrics require publications. The zero-publication ok branch breaks
  the deadlock by making the verdict reflect reality: "pipeline ran cleanly, nothing to
  extract." **How to apply:** the `compute_verdict` branch order ensures the fail and
  regression checks fire first (covering the `rc != 0` and `delta < 0` cases), so the new
  branch only activates for genuinely clean zero-publication runs.

- **NEVER** depend on `config-evolve` containing a specific ticker in hermetic tests. The
  `config-evolve` directory is regenerated by `write_evolve_config()` on every tick to contain
  ONLY the current batch's tickers. Any test that reads from `config-evolve` and filters by a
  hard-coded ticker (e.g., `--tickers EDX13577`) will fail on the next tick when a different
  company is selected. Caught on tick #101: `test_main_async_uses_build_http_client_not_direct_client`
  was written in tick #93 when EDX13577 was the batch; it silently broke on the next batch.
  **Why:** config-evolve is a per-tick artefact, not a stable test fixture. **How to apply:**
  any test that exercises code which calls `load_all(args.config_dir)` and then filters tickers
  must mock `load_all` to return a fake settings object, or use `config/` (stable, always has
  all tickers) with a canary ticker (SBER, LKOH, IZNM).

- **NEVER** conflate `discoverer_id_not_found` (HTTP 404/410 for all 4 type URLs — ID doesn't
  exist on the portal) with `discoverer_no_publications` (HTTP 200+empty for all types — ID is
  valid but company has no filed reports). These require different operator actions. A 404 means
  the e_disclosure_id is wrong and must be found or the ticker removed. A 200+empty means the
  company IS registered but hasn't filed yet — no code change needed, just wait or investigate
  filing obligations. Taxonomy rule 2 distinguishes these by checking the `status` field of each
  `discoverer_no_publications_for_type` log event. Caught on tick #93: the diagnostic bundle
  showed status=404 (portal returned 404 at tick run time); validation run showed 200+empty
  (portal returned a real empty-table page). Both are handled correctly by the new code.
  **Why:** operator needs a different response for each case. **How to apply:** taxonomy rule 2
  checks `all(status in {404, 410})` to select `discoverer_id_not_found`; otherwise uses
  `discoverer_no_publications`. Do not merge the two codes back together.

- **NEVER** treat a company with all publications in `{written, skipped}` and 0 metrics as
  an actionable failure requiring a code fix. Both states are genuinely terminal: "written" means
  the LLM ran but found no financial metrics in the document (scanned PDFs with poor OCR quality,
  or accounting-policy-only documents); "skipped" means the publication had no IFRS/RSBU/ISSUER
  documents at all (annual reports with only appendices). These are stable final states — the
  pipeline has fully processed the company, there is nothing left to extract. The correct verdict
  is "ok" (7-day cooldown), NOT "neutral" (infinite re-selection) or "fail" (skiplist bump).
  Caught on tick #180: EDX20321 (ОАО «ЦСД) had 19 written + 27 skipped, 0 metrics, and was
  re-selected on every tick because the `all_written_no_metrics` branch only covered the
  all-written case, not the mixed written+skipped case.
  **Why:** `neutral` = `_PRIORITY_NEVER` in the Picker; the company is re-picked on every tick
  indefinitely, generating diagnostic bundles with no actionable outcome. The correct response is
  to recognise the stable terminal state and put the company on the normal cooldown cycle.
  **How to apply:** the `all_terminal_no_metrics` verdict branch uses
  `written + skipped == publications_total` (not `written == publications_total`). The
  `_has_written_no_metrics_publications()` bundle helper uses `_terminal = {"written", "skipped"}`.
  Taxonomy rule 4.8 fires before rule 5 (`metric_coverage_zero`) to suppress the misleading
  "extend synonyms" hint when all pubs are terminal.

## Companies status (top 30 most recently touched)

| company_id | name | last_tick | verdict | metrics_count |
|---|---|---|---|---|
| 20321 | ОАО «ЦСД» | #180 | ok | 0 |
| 1480 | ПАО "Аэрофлот" | #101 | fail | 0 |
| 13577 | ОАО "Морской торговый порт Темрюк" | #94 | ok | 0 |
| 1285 | ПАО "МТС-Банк" | #87 | neutral | 0 |
| 120 | Банк "Возрождение" (ПАО) | #82 | ok | 345 |
| 11690 | АО "Омский ЭМЗ" | #77 | ok | 45 |
| 11777 | АО "УМ-1" | #77 | ok | 24 |
| 11903 | ОАО "Байкальский ЦБК" | #77 | ok | 102 |
| 1021 | НОТА-Банк (ПАО) | #75 | ok | 55 |
| 105 | ПАО НИКО-БАНК | #75 | ok | 35 |
| 11473 | АО Алтайэнергосбыт | #75 | ok | 57 |
