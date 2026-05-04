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
| pipeline_timeout | #77 | #77 | tick.py DEFAULT_PIPELINE_TIMEOUT_S 30→90 min; taxonomy pipeline_timeout code | yes |
| verdict_already_healthy | #75 | #75 | verdict.py ok-branch uses after.metrics_rows | yes |
| defunct_company_bootstrap | #74 | #74 | discoverer _BOOTSTRAP_CUTOFF, unpacker BadZipFile | yes |

## Patches log (reverse-chronological)

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
- **Files touched:**
  - `src/edx/cli.py` — added `--config-dir` and `--ticker` to `update` subparser; wired
    `ticker_filter` through to `_execute_pipeline_run`
  - `src/edx/evolve/taxonomy.py` — added `cli_startup_error` TaxonomyCode, hint, rule 0
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

## Companies status (top 30 most recently touched)

| company_id | name | last_tick | verdict | metrics_count |
|---|---|---|---|---|
| 11690 | АО "Омский ЭМЗ" | #77 | ok | 45 |
| 11777 | АО "УМ-1" | #77 | ok | 24 |
| 11903 | ОАО "Байкальский ЦБК" | #77 | ok | 102 |
| 1021 | НОТА-Банк (ПАО) | #75 | ok | 55 |
| 105 | ПАО НИКО-БАНК | #75 | ok | 35 |
| 11473 | АО Алтайэнергосбыт | #75 | ok | 57 |
