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
