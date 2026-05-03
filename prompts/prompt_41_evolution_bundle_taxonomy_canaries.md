# Промпт 41. Самоэволюция: Diagnostic Bundle, Failure Taxonomy, Canary baseline

> Зависимости: Patch 38–40.
> Этот патч превращает «голый» вердикт тика в **полноценный input для
> Claude Code** — собирает агрегированный Diagnostic Bundle (логи + срез
> БД + автоклассификация ошибок) и фиксирует canary baseline для
> anti-regression проверок.

## Цель

1. `evolve/bundle.py` — собирает каталог `evolution/runs/{tick_id}/`
   с per-company артефактами для агента.
2. `evolve/taxonomy.py` — анализирует `pipeline.log` + per-company
   снимок и классифицирует провалы по 10 кодам (см.
   [`PLAN_self_evolution.md` §7 таблица](../PLAN_self_evolution.md)).
3. `evolve/canaries.py` — снимает baseline для трёх «канареечных»
   тикеров (`SBER`, `LKOH`, `IZNM`) и валидирует их после патча.
4. Хук в `tick.py`: на любом не-`ok` overall verdict сразу строим
   bundle и переводим тик в `phase='claude_code'` (агент пока
   не подключён, тик закрывается verdict'ом без правок).

## Контекст

- Pipeline log — JSON-lines. Структурированные events:
  `discoverer_non_200`, `discoverer_no_publications_for_type`,
  `period_parser_unmatched`, `classifier_pages_classified`,
  `text_extractor_completed`, `metric_extract_completed`,
  `metric_extract_failed`, `publication_validated`,
  `orchestrator_replicator_failed`, etc.
- В state.sqlite поля важные для таксономии:
  `publications.status`, `publications.last_error`,
  `documents.is_machine_readable`, `documents.reporting_standard`,
  `qa_issues.code`, `qa_issues.message`, `metrics` row count.
- Bundle используется агентом — поэтому каждый файл должен быть
  читаемым человеком: JSON pretty-printed, log пофильтрован, SQL
  с заголовками колонок.

## Задачи

### 1. `src/edx/evolve/canaries.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from edx.evolve.snapshot import TickerSnapshot, snapshot_batch

CANARY_TICKERS = ("SBER", "LKOH", "IZNM")


def canary_baseline_path(state_db: Path) -> Path:
    return state_db.parent / "canary_baseline.json"


def take_canary_baseline(conn, target_path: Path) -> None:
    """Persist current snapshot of canary tickers to JSON."""
    ...


def load_canary_baseline(target_path: Path) -> dict[str, TickerSnapshot]:
    ...


@dataclass(frozen=True, slots=True)
class CanaryReport:
    ticker: str
    ok: bool
    notes: tuple[str, ...]


def check_canaries(
    conn,
    baseline_path: Path,
) -> list[CanaryReport]:
    """For each canary, compare current snapshot to baseline.

    A canary is OK if:
      - publications_total >= baseline.publications_total
      - metrics_rows >= baseline.metrics_rows - 0   (allow 0 only for now)
      - publications_by_status['written'] >= baseline['written']
    Otherwise it's a regression — return ok=False with diagnostic notes.
    """
    ...
```

Замечание про baseline:
- baseline создаётся **один раз** перед первым тиком. Если файла нет,
  bundle.assemble не проверяет канарейки и логирует warning
  `canary_baseline_missing`. Оператор сам запускает `edx evolve canary
  capture` (CLI этой команды добавим в Patch 43; пока — функция
  `take_canary_baseline` экспортируется и вызывается тестами).

### 2. `src/edx/evolve/taxonomy.py`

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

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


@dataclass(frozen=True, slots=True)
class TaxonomyEntry:
    ticker: str
    code: TaxonomyCode
    evidence: tuple[str, ...]    # 3-5 sample log lines / SQL rows
    hint: str                    # human-readable hint for the agent


def classify_failures(
    log_path: Path,
    state_slice: dict,           # per-ticker dict from state-slice
    failing_tickers: list[str],
) -> list[TaxonomyEntry]:
    """Return one entry per failing ticker, in input order.

    Hints (text in `hint` field) — see PLAN_self_evolution §7 table.
    Be conservative: if ambiguous → "unknown" with empty hint."""
    ...
```

Implementation tips:
- Регэкспы для каждого условия. Например для
  `unique_constraint`: `re.search(r'"event":\s*"metric_extract_failed".*UNIQUE constraint', line)`.
- Для `metric_coverage_zero/low`: проверяем
  `qa_issues.code == 'incomplete'` + `metrics_rows`.
- Возвращаем именно `tuple` (immutable) для evidence — удобнее
  сериализовать в JSON через кастомный encoder.

### 3. `src/edx/evolve/bundle.py`

```python
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from dataclasses import asdict
from pathlib import Path

from edx.evolve.canaries import check_canaries, canary_baseline_path
from edx.evolve.csv_loader import CompanyRow
from edx.evolve.snapshot import TickerSnapshot
from edx.evolve.taxonomy import classify_failures, TaxonomyEntry
from edx.evolve.verdict import TickerVerdict


def assemble(
    bundle_dir: Path,
    *,
    batch: list[CompanyRow],
    snaps_before: dict[str, TickerSnapshot],
    snaps_after: dict[str, TickerSnapshot],
    verdicts: dict[str, TickerVerdict],
    log_path: Path,
    state_db: Path,
    memory_md_path: Path = Path("evolution/MEMORY.md"),
) -> dict:
    """Build the Diagnostic Bundle for one tick. Returns a manifest dict
    that can be logged."""
```

Что должно лежать в `bundle_dir/` после успеха:

```
batch.json                # [{company_id, name, ticker, profile, verdict_code}, …]
snap_before.json          # уже создан в Patch 40, NOT overwritten
snap_after.json           # уже создан
pipeline.log              # копируется из бэкграунда (если ещё не там)
pipeline.log.errors       # grep level=error|warning, max 500KB
state-slice.sql           # см. ниже
state-slice.txt           # human-readable rendering
failure_taxonomy.json     # [TaxonomyEntry, …]
canary_baseline.json      # копия из data/ (для контекста)
canary_check.json         # текущий результат check_canaries
memory_snapshot.md        # копия evolution/MEMORY.md (FROZEN на момент)
recent_commits.txt        # `git log --oneline -20`
prompt.md                 # шаблон для Claude Code (см. Patch 42, для совместимости пока — пустой stub с TODO)
```

`state-slice.sql` — генерируем через `sqlite3` CLI:

```bash
.headers on
.mode list
.separator |

SELECT * FROM publications WHERE ticker IN ('EDX1210','EDX38588','EDX2541');
SELECT * FROM documents     WHERE publication_id IN (SELECT publication_id FROM publications WHERE ticker IN (...));
SELECT * FROM metrics       WHERE ticker IN (...);
SELECT * FROM qa_issues     WHERE ticker IN (...);
```

(Можно генерировать в Python через `conn.execute(...).fetchall()` —
не зависим от наличия `sqlite3` CLI на хосте.)

`pipeline.log.errors` — простой grep:
```python
ERROR_LEVELS = {'"level": "error"', '"level": "warning"'}
with log_path.open() as fin, errors_path.open("w") as fout:
    for line in fin:
        if any(lvl in line for lvl in ERROR_LEVELS):
            fout.write(line)
```

`recent_commits.txt`: `subprocess.check_output(["git", "log",
"--oneline", "-20"], text=True)`. Если git недоступен — пишем «git
unavailable» (тесты на изолированных каталогах).

`prompt.md` (для Patch 41 — пока stub):
```markdown
# Tick #{tick_id} — Diagnostic Bundle

> This is a Patch 41 stub. Patch 42 wires Claude Code to consume it.
>
> Failing tickers: {list}
> Failure classes: {list}
```

### 4. Хук в `tick.py`

После расчёта verdicts:

```python
overall = _aggregate_verdict(verdicts)

if overall != "ok":
    # build bundle for future Claude Code consumption
    failing = [t for t, v in verdicts.items() if v.code in ("fail","regression")]
    state_slice = _state_slice_for_tickers(conn, list(verdicts))
    taxonomy = classify_failures(log_path, state_slice, failing)
    (bundle_dir / "failure_taxonomy.json").write_text(
        json.dumps([asdict(e) for e in taxonomy], ensure_ascii=False, indent=2)
    )
    bundle.assemble(bundle_dir, batch=batch, ...)

    repo.update_tick(tick_id, phase="claude_code")
    # Patch 41: claude code не подключён. Тик закрывается без агента.
    repo.update_tick(tick_id,
                     phase="failed",
                     verdict=overall,
                     finished_at=...,
                     bundle_path=str(bundle_dir))
else:
    # как было
    ...
```

Внимание: bundle не должен падать, если в state нет ни одной
publications-row для тикера (новая компания, ещё ничего не
скачивалось). В этом случае `state-slice.sql` пустой, файл всё равно
создаётся (с заголовком и без строк).

### 5. Тесты

`tests/evolve/test_taxonomy.py`:
- параметризованные сценарии — фейковый log + state-slice → ожидаемый
  TaxonomyCode.
- негативный кейс: лог без error-строк → `"unknown"`.

`tests/evolve/test_bundle.py`:
- `test_bundle_creates_all_files`: смокаем минимальный набор входов,
  все 9 файлов должны существовать и быть валидным JSON где надо.
- `test_bundle_pipeline_log_filtered`: с большим log file (фейковым)
  на выходе errors-файл содержит только error+warning.
- `test_bundle_memory_snapshot_frozen`: `memory_snapshot.md` дублирует
  байт-в-байт `evolution/MEMORY.md` на момент вызова, дальнейшие
  изменения MEMORY.md его не трогают.
- `test_bundle_handles_empty_state`: тикер без строк в `publications`
  не валит сборку.

`tests/evolve/test_canaries.py`:
- `test_canary_baseline_round_trip`.
- `test_canary_check_detects_metrics_drop`.
- `test_canary_check_passes_on_no_change`.

## Acceptance criteria

- `make lint` ✅ `make typecheck` ✅ `make test` ✅
- Запуск `edx evolve tick` на трёх компаниях с заведомо проблемным id
  (например, новой «иэк холдинг» 38588) → создаёт `evolution/runs/{N}/`
  со всеми перечисленными файлами. Открыть `failure_taxonomy.json` —
  адекватные коды.
- `git status` чисто, никаких изменений вне `evolution/runs/`.

## Риски и инварианты

- Bundle pipeline ВСЕГДА синхронный, никаких background processes.
- НИКОГДА не помещаем в bundle .env, `data/processed/_llm_cache/`
  целиком (это могут быть гигабайты), `output/*.xlsx`.
- `state-slice.sql` обрезается по списку батч-тикеров (3 шт.) — не
  дампим всю БД.
- Размер bundle: должен умещаться в 5–15 MB. Pipeline.log >100MB
  обрезаем до последних 50_000 строк (всё равно есть pipeline.log.errors).

## Что класть в MEMORY.md

Anti-pattern: «Не делайте таксономию на регэкспах ТОЛЬКО по тексту лога —
сверяйте со state-slice. Лог может быть зашумлён warnings из других
тикеров батча, и вы припишете чужой failure».
