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
| _no entries yet_ | | | | |

## Patches log (reverse-chronological)

_no entries yet_

## Anti-patterns

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
| _no entries yet_ | | | | |
