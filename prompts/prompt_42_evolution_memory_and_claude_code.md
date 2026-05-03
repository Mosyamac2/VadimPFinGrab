# Промпт 42. Самоэволюция: модуль памяти + headless Claude Code runner

> Зависимости: Patch 38–41.
> Этот патч ВКЛЮЧАЕТ агента — добавляет обёртку `claude -p`,
> permissions sandbox, slash-команду `/edx-evolve-fix` и валидатор
> «MEMORY.md был обновлён в этом тике». **На этом этапе никаких
> commit'ов и push'ей пока нет — verdict gate и git-логика приземляются
> в Patch 43.**

## Цель

1. `evolve/memory.py` — read/append `evolution/MEMORY.md` со
   strict-парсером, чтобы детектировать «не обновили память» в gate.
2. `evolve/claude_runner.py` — обёртка `subprocess.run("claude -p")`
   с парсингом `stream-json`, бюджетом, таймаутом, persisted трейсом.
3. `.claude/settings.evolve.json` — permissions для headless-режима
   (deny-list жёсткий, allow-list узкий).
4. `.claude/commands/edx-evolve-fix.md` — slash-команда с обязательными
   STEP 0 (read MEMORY) и STEP 4 (update MEMORY). Содержимое подробно
   прописано в [`PLAN_self_evolution.md` §8.3](../PLAN_self_evolution.md).
5. Хук в `tick.py`: на failing batch вызываем агента, складываем
   результат, всё ещё без commit'а.

## Контекст

- Claude Code CLI (Node 20+, `npm install -g @anthropic-ai/claude-code`)
  принимает permissions через `--permission-mode acceptEdits`,
  `--allowedTools "Read,Edit,…"`, `.claude/settings.json` per-project.
- `claude -p PROMPT --output-format stream-json` пишет в stdout
  поток JSON-объектов. Финальная строка имеет тип `result` и поля
  `total_cost_usd`, `num_turns`, `session_id`, `is_error`.
- Проект уже содержит `.claude/settings.local.json` с permissions
  оператора. Не трогаем его — он не для headless-режима.

## Задачи

### 1. `src/edx/evolve/memory.py`

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MEMORY_PATH = Path("evolution/MEMORY.md")

PATCH_HEADER_RE = re.compile(
    r"^### evolve\((?P<tick>\d+)\)\s+—\s+(?P<date>\d{4}-\d{2}-\d{2})\s+—\s+"
    r"(?P<failure_class>[\w_]+)\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class MemoryDigest:
    raw: str
    patch_entries: int                # count of `### evolve(N)` headers
    last_tick: int | None             # max(tick) seen
    failure_classes: frozenset[str]
    anti_patterns_count: int          # rough count of bullet items in section


def read(path: Path = MEMORY_PATH) -> MemoryDigest: ...


def has_new_entry_since(
    before_raw: str,
    after_raw: str,
    tick_id: int,
) -> bool:
    """Returns True if `after_raw` contains a ### evolve(tick_id) header
    that `before_raw` does not. False otherwise.

    This is the primary gate check — if the agent forgot to update
    MEMORY.md, the tick is marked failed."""
    ...


def diff_summary(before_raw: str, after_raw: str) -> str:
    """Cheap textual diff for logging. Not for git."""
    ...
```

### 2. `src/edx/evolve/claude_runner.py`

```python
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ClaudeRunResult:
    session_id: str | None
    is_error: bool
    cost_usd: float
    turns: int
    duration_seconds: float
    modified_files: tuple[str, ...]   # via `git diff --name-only` after run
    stream_path: Path                 # .jsonl with full transcript
    summary_path: Path | None         # evolution/runs/{tick_id}/SUMMARY.md
    last_assistant_text: str          # last 2KB for logs


def run_agent(
    *,
    bundle_dir: Path,
    tick_id: int,
    project_root: Path,
    settings_path: Path = Path(".claude/settings.evolve.json"),
    slash_command_args: str | None = None,   # e.g. "{tick_id}"
    budget_usd: float = 2.0,
    max_turns: int = 25,
    timeout_seconds: int = 30 * 60,
) -> ClaudeRunResult:
    """Run `claude -p '/edx-evolve-fix {tick_id}' --output-format stream-json`.

    Steps:
      1. Snapshot project root (`git rev-parse HEAD`) for later diff.
      2. Execute claude with --permission-mode acceptEdits and
         --add-dir bundle_dir.
      3. Stream stdout into bundle_dir/claude.jsonl line-by-line.
         As each JSON object arrives:
           - if total cost > budget_usd → SIGTERM the subprocess.
           - if num_turns exceeds max_turns → SIGTERM.
      4. On exit: parse final `result` event for cost / turns / session.
      5. Compute modified_files via `git diff --name-only`.
      6. Read SUMMARY.md if present.

    Never raises on agent error — returns ClaudeRunResult with is_error=True.
    """
    ...
```

Под капотом — `subprocess.Popen` с `stdout=subprocess.PIPE,
text=True, bufsize=1`. Чтение строка-в-строку, парсинг JSON, при
превышении бюджета — `proc.terminate()` затем `proc.wait(timeout=10)`,
далее `proc.kill()` если нужно.

`OAuth-токен` берётся из переменной `CLAUDE_CODE_OAUTH_TOKEN` (env).
Если её нет — `ClaudeRunResult(is_error=True, …)` с notes
«claude_token_missing». Это позволяет тестам прогоняться без реального
токена.

### 3. `.claude/settings.evolve.json` (новый файл)

Точечный sandbox для headless-режима. **Совершенно отдельный** от
`.claude/settings.local.json` оператора.

```json
{
  "permissions": {
    "allow": [
      "Read(./)",
      "Glob",
      "Grep",
      "Edit(src/**)",
      "Edit(config/**)",
      "Edit(tests/**)",
      "Edit(prompts/**)",
      "Edit(evolution/MEMORY.md)",
      "Write(evolution/runs/*/SUMMARY.md)",
      "Bash(.venv/bin/python -m pytest *)",
      "Bash(.venv/bin/python -m ruff check *)",
      "Bash(.venv/bin/python -m mypy src)",
      "Bash(.venv/bin/python *)",
      "Bash(.venv/bin/edx update *)",
      "Bash(make lint *)",
      "Bash(make typecheck *)",
      "Bash(make test *)",
      "Bash(git diff *)",
      "Bash(git status *)",
      "Bash(git log *)"
    ],
    "deny": [
      "Edit(.env*)",
      "Write(.env*)",
      "Edit(deploy/**)",
      "Write(deploy/**)",
      "Edit(.git/**)",
      "Write(.git/**)",
      "Edit(.claude/**)",
      "Write(.claude/**)",
      "Edit(evolution/runs/**)",
      "Bash(git push *)",
      "Bash(git reset --hard *)",
      "Bash(git checkout *)",
      "Bash(git branch *)",
      "Bash(git commit *)",
      "Bash(git rebase *)",
      "Bash(rm -rf *)",
      "Bash(rm /*)",
      "Bash(curl *)",
      "Bash(wget *)",
      "Bash(npm *)",
      "Bash(pip install *)",
      "Bash(.venv/bin/pip install *)",
      "Bash(sudo *)",
      "WebFetch",
      "WebSearch"
    ]
  }
}
```

### 4. `.claude/commands/edx-evolve-fix.md` (новый файл)

Содержимое — буквально как в [`PLAN_self_evolution.md` §8.3](../PLAN_self_evolution.md).
Скопировать и проверить:
- argument-hint указан (`<tick_id>`);
- allowed-tools перекликается с settings.evolve.json (минус git-write);
- STEP 0 явно требует прочесть `evolution/MEMORY.md` ДО любых
  изменений;
- STEP 4 явно требует append к `evolution/MEMORY.md` шаблоном
  `### evolve($1) — {date} — {failure_class}`;
- STEP 5 запрещает агенту делать commit/push.

### 5. Хук в `tick.py`

После сборки bundle (Patch 41) и `phase='claude_code'`:

```python
import os
from edx.evolve.memory import read as read_memory, has_new_entry_since
from edx.evolve.claude_runner import run_agent
from edx.evolve.daily_budget import current_day_spend  # см. ниже

# 1. Daily budget gate
spend = repo.daily_cost_usd(date.today().isoformat())
daily_cap = float(os.environ.get("EDX_EVOLVE_DAILY_BUDGET_USD", "25"))
if spend >= daily_cap:
    repo.update_tick(tick_id, phase="failed", verdict="skipped_budget",
                     error_summary=f"daily budget reached: ${spend:.2f}/${daily_cap:.2f}",
                     finished_at=...)
    return tick_id

# 2. Запоминаем MEMORY перед стартом
memory_before = MEMORY_PATH.read_text(encoding="utf-8")

# 3. Run agent
tick_budget = float(os.environ.get("EDX_EVOLVE_TICK_BUDGET_USD", "2"))
agent = run_agent(
    bundle_dir=bundle_dir, tick_id=tick_id,
    project_root=Path("."),
    slash_command_args=str(tick_id),
    budget_usd=tick_budget,
    max_turns=25,
)

repo.update_tick(tick_id,
    claude_session=agent.session_id,
    claude_cost_usd=agent.cost_usd,
    claude_turns=agent.turns,
)

# 4. Memory check
memory_after = MEMORY_PATH.read_text(encoding="utf-8")
memory_updated = has_new_entry_since(memory_before, memory_after, tick_id)

# 5. Записываем итог тика. На этом этапе:
#    - НИКАКИХ commit'ов: gate-логика появится в Patch 43.
#    - Если modified_files == 0 → verdict='fail', reason='claude_no_changes'.
#    - Если modified_files > 0 и memory_updated == False → verdict='fail',
#      reason='memory_not_updated'.
#    - В остальных случаях — verdict='neutral' (gate в Patch 43 решит окончательно).
verdict_for_tick = (
    "fail" if not agent.modified_files
    else "fail" if not memory_updated
    else "neutral"
)
repo.update_tick(tick_id, phase="failed" if verdict_for_tick == "fail" else "verdict",
                 verdict=verdict_for_tick, finished_at=...,
                 error_summary=... if verdict_for_tick == "fail" else None)
return tick_id
```

### 6. Daily budget helper

`src/edx/evolve/daily_budget.py` — тонкая обёртка над
`EvolutionRepo.daily_cost_usd`. Не плодим лишний слой, можно даже
функцию в `tick.py`. Главное — `EDX_EVOLVE_DAILY_BUDGET_USD` env var
с дефолтом 25.0 и `EDX_EVOLVE_TICK_BUDGET_USD` с дефолтом 2.0.

### 7. Тесты

`tests/evolve/test_memory.py`:
- `test_has_new_entry_since`: before без `### evolve(5)`, after с — True.
- `test_has_new_entry_since_other_tick`: before без `evolve(5)`, after
  только с `evolve(6)` — False (нужен ровно tick_id).
- `test_read_digest_counts_patches`.
- `test_diff_summary_basic`.

`tests/evolve/test_claude_runner.py` — без реального claude:
- monkeypatch `subprocess.Popen` с фейковым stdout, который возвращает
  валидные stream-json строки (assistant + result).
- `test_runner_parses_result`: cost/turns/session_id вытащены.
- `test_runner_kills_on_budget`: stream подаёт серию assistant с
  растущим total_cost_usd; runner SIGTERMит когда переходит порог.
- `test_runner_no_token_returns_error`: env var отсутствует → возвращает
  is_error=True без вызова Popen.
- `test_runner_modified_files_detected`: monkeypatch `git diff --name-only`
  через subprocess.run mock.

`tests/evolve/test_settings_evolve_schema.py`:
- читаем `.claude/settings.evolve.json` (новый, lежит в репозитории),
  валидируем что allow/deny списки не пересекаются и обязательные
  deny-правила присутствуют (`Bash(git push *)`, `Edit(.env*)`,
  `Edit(deploy/**)`).

`tests/evolve/test_slash_command_present.py`:
- читаем `.claude/commands/edx-evolve-fix.md`, проверяем наличие
  ключевых якорей: «STEP 0», «evolution/MEMORY.md», «STEP 4», «STEP 5»,
  «DO NOT commit».

## Acceptance criteria

- `make lint` ✅ `make typecheck` ✅ `make test` ✅
- `python -c "from edx.evolve.memory import has_new_entry_since; print('ok')"` → `ok`.
- `cat .claude/settings.evolve.json | jq '.permissions.deny' | grep -c "git push"` → ≥ 1.
- При запуске `edx evolve tick` без `CLAUDE_CODE_OAUTH_TOKEN`:
  тик доходит до `phase='claude_code'`, агент возвращает is_error,
  тик завершается verdict='fail' с error_summary='claude_token_missing'.
- В bundle присутствует `claude.jsonl` (пустой если без токена) и
  `prompt.md` со ссылкой на slash-команду.

## Риски и инварианты

- Headless-обёртка НЕ должна давать агенту читать `.env` — это в
  deny-листе settings.evolve.json. Дополнительно: `--add-dir` — только
  `bundle_dir` и `project_root`; `~/` НЕ передаётся.
- Бюджет проверяется на КАЖДОМ assistant-message, не только в конце.
  Это ловит runaway циклы.
- `claude.jsonl` не должен содержать base64-PDF — фильтруем `image`
  или `pdf` payload-блоки на лету при записи.
- НЕ запускаем claude из CLI handler без явного env флага
  `EDX_EVOLVE_AGENT_ENABLED=1` (по умолчанию выключен — для
  безопасности патчей 38–43; включится в Patch 46). Это feature flag.

## Что класть в MEMORY.md

Anti-pattern: «Если хочется расширить allow-list в settings.evolve.json,
сначала проверь, не добавляешь ли инструмент с побочными эффектами
(curl, npm, sudo) — это разрешит агенту скачать произвольный код».

Anti-pattern: «Не передавайте `--add-dir ~/` в claude-runner ни при
каких обстоятельствах — это даст агенту доступ к чужим проектам и
~/.ssh/ если CLI на personal машине».
