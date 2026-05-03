# Промпт 43. Самоэволюция: полный verdict gate, git-обёртка с auto-merge в master, расширенный CLI

> Зависимости: Patch 38–42.
> Этот патч превращает «проксирующего» агента из Patch 42 в
> ПОЛНОЦЕННЫЙ self-evolve loop с автоматическим коммитом и merge в
> master при прохождении gate'а. ⚠️ Здесь же приземляется feature flag
> `EDX_EVOLVE_AGENT_ENABLED=1`, иначе вся логика остаётся в режиме
> dry-run (тик закрывается с verdict='neutral' без push'ей).

## Цель

1. `evolve/git_ops.py` — изолированная обёртка над git с **жёстким
   whitelist'ом**: новые ветки `evolve/tick-N`, fast-forward в master,
   force-`git branch -D` только своих веток, push, нормализованные
   commit-messages.
2. Полноценный verdict gate в `tick.py`: tests + canaries + batch
   improvement + memory update.
3. Расширенный CLI: `edx evolve status`, `replay`, `report`, `reset`,
   `memory show/verify/compact`, `canary capture`.
4. Тесты gate-логики на синтетических сценариях (no real git).

## Контекст

- Исходное состояние репо: master + один origin remote
  (`git remote get-url origin`). Нет hooks, blocking pre-push'а.
- В Patch 42 уже:
  - bundle собран,
  - агент запущен с deny на git-write,
  - memory_after проверен на наличие новой записи.
- `auto-evolve`-ветки: формат `evolve/tick-N` (косая черта — ОК для git).
- Канарейный baseline существует на VPS после `edx evolve canary capture`
  (новая команда из этого патча).

## Задачи

### 1. `src/edx/evolve/git_ops.py`

```python
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

ALLOWED_FILES_GLOBS = (
    "src/edx/**",
    "config/**",
    "tests/**",
    "prompts/**",
    "evolution/MEMORY.md",
)

PROHIBITED_FILES_GLOBS = (
    ".env*",
    "deploy/**",
    ".git/**",
    ".claude/settings.local.json",
    "evolution/runs/**",
    "config-evolve/**",
    "data/**",
    "output/**",
    "logs/**",
)


@dataclass(frozen=True, slots=True)
class GitMergeResult:
    branch: str
    commit_sha: str | None        # None on rollback
    pushed: bool
    rolled_back: bool
    notes: tuple[str, ...]


def current_branch(cwd: Path) -> str:
    ...


def create_tick_branch(cwd: Path, tick_id: int, base: str = "master") -> str:
    """`git checkout -b evolve/tick-{tick_id} master`. Fails if branch exists."""
    ...


def whitelist_violations(cwd: Path) -> list[str]:
    """Return list of changed files (vs. master) that violate whitelist.
    Empty list = OK."""
    ...


def stage_changes(cwd: Path) -> list[str]:
    """`git add` on whitelisted files only. Returns staged paths."""
    ...


def commit_and_merge(
    cwd: Path,
    tick_id: int,
    message: str,
    push: bool = True,
    remote: str = "origin",
) -> GitMergeResult:
    """One-shot:
      1. assert current branch == evolve/tick-{tick_id}
      2. fail-soft if no changes (return notes=('no_changes',))
      3. validate whitelist (no prohibited paths in diff)
      4. stage + commit on tick branch
      5. checkout master
      6. git merge --ff-only evolve/tick-{tick_id}   (fail if not FF)
      7. if push: git push remote master
      8. on any failure: git checkout master, git branch -D evolve/tick-N, return rolled_back=True
    """
    ...


def abandon_branch(cwd: Path, tick_id: int) -> None:
    """`git checkout master && git branch -D evolve/tick-{tick_id}`.
    Idempotent (no-op if branch doesn't exist)."""
    ...
```

**Жёсткие инварианты:**

- Не делает `git config` ничего — пользуется существующей.
- Не делает `git push --force`.
- Не даёт указать `branch=master` в `create_tick_branch`.
- Логирует каждое subprocess-обращение через structlog
  `evolve.git_ops`.
- При неудаче `git push` — НЕ откатывает master commit (оператор
  потом разрулит). Но фиксирует `pushed=False, notes=('push_failed',
  …)` чтобы tick не считался успешным.

### 2. Полный verdict gate в `tick.py`

Меняем хвост `run_one_tick` — после `run_agent`:

```python
agent_enabled = os.environ.get("EDX_EVOLVE_AGENT_ENABLED") == "1"

if not agent_enabled:
    # dry-run mode: оставляем patch 42 поведение
    repo.update_tick(tick_id, phase="failed",
                     verdict="neutral",
                     error_summary="agent_disabled (EDX_EVOLVE_AGENT_ENABLED!=1)",
                     finished_at=...)
    return tick_id

# A. ничего не поменял?
if not agent.modified_files:
    git_ops.abandon_branch(Path("."), tick_id)
    repo.update_tick(tick_id, phase="failed", verdict="fail",
                     error_summary="claude_no_changes", finished_at=...)
    bump_skiplist_for_failing(repo, batch, verdicts)
    return tick_id

# B. memory не обновлён?
if not memory_updated:
    git_ops.abandon_branch(Path("."), tick_id)
    repo.update_tick(tick_id, phase="failed", verdict="fail",
                     error_summary="memory_not_updated", finished_at=...)
    bump_skiplist_for_failing(repo, batch, verdicts)
    return tick_id

# C. tests gate
if not _run_make("test", cwd=Path(".")):
    git_ops.abandon_branch(Path("."), tick_id)
    repo.update_tick(tick_id, phase="failed", verdict="regression_tests",
                     error_summary="make_test_red", finished_at=...)
    bump_skiplist_for_failing(repo, batch, verdicts)
    return tick_id

# D. lint + typecheck (не блокирующие, но логируем)
_run_make("lint",      cwd=Path("."))   # warning only
_run_make("typecheck", cwd=Path("."))   # warning only

# E. re-run на батче
res2 = run_pipeline_on_batch(
    tickers=[c.synthetic_ticker for c in batch],
    config_dir=Path("config-evolve"),
    log_path=bundle_dir / "pipeline.log.retry",
)
snaps_retry = snapshot_batch(conn, [c.synthetic_ticker for c in batch])
verdicts_retry = {t: compute_verdict(snaps_before[t], snaps_retry[t],
                                     pipeline_returncode=res2.returncode)
                  for t in snaps_before}

# F. canary check
canary = check_canaries(conn, canary_baseline_path(state_db))
canary_ok = all(c.ok for c in canary)

# G. improvement check
improved = any(verdicts[t].code in ("fail","regression")
               and verdicts_retry[t].code == "ok"
               for t in verdicts)
not_regressed = all(verdicts_retry[t].code != "regression" for t in verdicts)

# H. финальный verdict
final = (
    "ok" if (improved and not_regressed and canary_ok)
    else "regression_canary" if not canary_ok
    else "regression" if not not_regressed
    else "fail"   # patch не починил ничего
)

if final == "ok":
    merge_res = git_ops.commit_and_merge(
        cwd=Path("."),
        tick_id=tick_id,
        message=_compose_commit_message(tick_id, batch, agent),
        push=True,
    )
    if merge_res.rolled_back or not merge_res.pushed:
        # gate прошёл, но git/push сломались — переразвернуть тик в FAIL
        repo.update_tick(tick_id, phase="failed", verdict="fail",
                         error_summary=f"git_failed: {merge_res.notes}",
                         finished_at=...)
        return tick_id
    repo.update_tick(tick_id, phase="done", verdict="ok",
                     verdicts_json=..., commit_sha=merge_res.commit_sha,
                     finished_at=...)
    merge_evolve_xlsx_into_main([c.synthetic_ticker for c in batch])
    return tick_id

# не "ok"
git_ops.abandon_branch(Path("."), tick_id)
repo.update_tick(tick_id, phase="failed", verdict=final,
                 error_summary=_format_failure_summary(verdicts_retry, canary),
                 finished_at=...)
bump_skiplist_for_failing(repo, batch, verdicts_retry)
return tick_id
```

`_compose_commit_message`:

```
evolve(N): batch [EDX1210,EDX38588,EDX2541]

failure_classes: [period_unparseable, metric_coverage_zero]
companies improved: [EDX38588]
companies neutral: [EDX1210, EDX2541]
canary check: ok

Claude Code session: 0193-…
Cost: $0.83  Turns: 14
Updated evolution/MEMORY.md with new entry evolve(N).
```

### 3. xlsx merge — `evolve/xlsx_merge.py`

```python
def merge_evolve_xlsx_into_main(tickers: list[str], *,
                                evolve_xlsx: Path = Path("output/e-disclosure-evolve.xlsx"),
                                main_xlsx: Path = Path("output/e-disclosure-new.xlsx"),
                                ) -> bool:
    """Append/upsert rows for `tickers` from evolve_xlsx into main_xlsx.

    Behaviour:
      - if main_xlsx is locked (.~lock.xlsx exists) → return False, no error
      - if evolve_xlsx absent → return False
      - else: open both via openpyxl, upsert by composite key
        (ticker, reporting_date, period_type, reporting_standard, metric_name)
      - preserve other tickers' rows in main_xlsx
      - write atomic: write to main_xlsx.tmp, fsync, rename
    """
```

Если xlsx-merge сложноват — приземление можно отложить в Patch 45.
Поставьте feature-flag (`EDX_EVOLVE_MERGE_XLSX=1`), по умолчанию
выключен. На MVP: state.sqlite — основной источник истины, xlsx
переподписывается общим `edx export-excel` оператором по cron'у.

### 4. CLI расширения

```python
# evolve status [--limit N] [--json]
# evolve replay --tick-id N [--no-claude]
# evolve report
# evolve reset --company-id ID
# evolve memory show
# evolve memory verify
# evolve memory compact
# evolve canary capture
```

`status` — таблица последних N тиков (tick_id, started_at, batch,
verdict, cost_usd, commit_sha).

`replay` — переиспользует существующий bundle (`evolution/runs/N/`),
подменяет `csv_loader` чтобы загрузить тех же companies, прогоняет
заново; с флагом `--no-claude` пропускает агента (только baseline).
Полезно для дебага git-обёртки.

`report` — агрегаты:
- ticks за 7 дней по verdict;
- среднее `cost_usd` для verdict='ok';
- топ-10 failure-классов;
- skiplist size;
- сегодняшний бюджет vs cap.

`reset --company-id ID` — `repo.reset(company_id)`.

`memory show` — `cat evolution/MEMORY.md`.

`memory verify` — парсит секции, для каждой `Files touched: …` проверяет
существование. Печатает stale-записи. Не редактирует MEMORY.md.

`memory compact` — interactive (требует подтверждения!). Архивирует в
`evolution/MEMORY.archive-{date}.md`, ужимает старшие 50% патчей в
сводку.

`canary capture` — снимает текущий snapshot SBER/LKOH/IZNM в
`data/canary_baseline.json`. Печатает что снял.

### 5. Тесты

`tests/evolve/test_git_ops.py` — на временных git-репах (`tmp_path`):
- `test_create_tick_branch_basic`.
- `test_create_tick_branch_master_blocked`: passing `base='master'` ОК,
  `tick_id=0` или target_branch='master' — отказ.
- `test_whitelist_blocks_env`: коммитить `.env` — violations not empty.
- `test_commit_and_merge_ff_only`: master ушёл вперёд → `rolled_back=True`,
  `pushed=False`.
- `test_commit_and_merge_no_changes`: tick branch без diff →
  `notes=('no_changes',)`, `rolled_back=False`, `pushed=False`.
- `test_abandon_branch_idempotent`.
- `test_push_failure_rolls_back_softly`: monkeypatch `git push` чтобы
  падал → master сохраняет коммит, `pushed=False`.

`tests/evolve/test_gate_orchestration.py` — высокоуровневые сценарии,
монипатчим:
- `run_pipeline_on_batch`,
- `run_agent`,
- `git_ops.commit_and_merge`,
- `check_canaries`.
И прогоняем все 7 веток (no_changes / memory_not_updated /
tests_red / canary_red / no_improvement / regression / ok).

`tests/evolve/test_cli_evolve_subcommands.py`:
- `evolve status` парсит вывод;
- `evolve reset` мутирует skiplist;
- `evolve memory show` печатает содержимое;
- `evolve memory verify` помечает stale-запись на отсутствующий файл.

## Acceptance criteria

- `make lint` ✅ `make typecheck` ✅ `make test` ✅
- `EDX_EVOLVE_AGENT_ENABLED=0 .venv/bin/edx evolve tick` — заканчивается
  verdict='neutral' БЕЗ touch'а master.
- На pre-prod репо (с тестовым origin'ом) при `EDX_EVOLVE_AGENT_ENABLED=1`
  и предсозданном «исправляющем» monkey-patch — gate проходит, master
  получает один новый commit с правильным сообщением, ветка
  `evolve/tick-N` удалена.
- `evolve memory verify` на свежем MEMORY.md (только заголовки) — печатает
  «no entries to verify».

## Риски и инварианты

- Любой crash после `git checkout master` (но до конца merge'а) НЕ
  должен оставлять master в полусломанном состоянии. Используем
  атомарную последовательность через `try/except` и `git reset --hard
  HEAD~0` с записью прежнего sha.
- НИКАКОЙ `git push --force-with-lease`. Только обычный push.
- merge-сообщение не содержит base64, не упоминает file_id Drive,
  не содержит ничего из `.env`.
- При `EDX_EVOLVE_AGENT_ENABLED!=1` ветка `evolve/tick-N` НЕ создаётся
  (мы в dry-run).

## Что класть в MEMORY.md

Anti-pattern: «`git_ops.commit_and_merge` НЕЛЬЗЯ модифицировать так,
чтобы он fall-back делал не-FF merge — это сломает audit-trail master'а».

Anti-pattern: «`git_ops.whitelist_violations` нельзя релаксить под
конкретный фикс — если агенту нужен `deploy/`, это сигнал, что фикс
неверный (deploy/ — оператор-only)».
