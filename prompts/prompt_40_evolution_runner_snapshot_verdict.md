# Промпт 40. Самоэволюция: Runner, Snapshot, Verdict + первая версия CLI `edx evolve tick`

> Зависимости: Patch 38 (БД), Patch 39 (Picker/Synth).
> Этот патч даёт ПЕРВУЮ работающую версию `edx evolve tick`, которая:
> - выбирает батч,
> - синтезирует config-evolve,
> - запускает текущий `edx update` на батче,
> - снимает per-company snapshot до/после,
> - выносит вердикт.
>
> Claude Code здесь ЕЩЁ НЕ ПОДКЛЮЧЁН. На failure тик просто фиксирует
> вердикт и выходит. Это нужно, чтобы можно было ручками гонять `edx
> evolve tick` и убеждаться, что инфраструктура работает на реальных
> данных, прежде чем подключать агента.

## Цель

1. `evolve/snapshot.py` — детерминированный снимок per-ticker метрик
   из state.sqlite.
2. `evolve/runner.py` — обёртка `subprocess.run` для одного прогона
   `edx update` на батче.
3. `evolve/verdict.py` — функция сравнения двух снимков → enum
   `VerdictCode`.
4. `evolve/tick.py` — оркестратор одного тика, использующий все
   модули из Patch 39 + новые.
5. CLI `edx evolve tick` (новая subcommand-группа `evolve`).

## Контекст

- `RunsRepo` пишет `runs.stats_json` после каждого пайплайн-прогона —
  можно вычислять delta из него, но **проще** считать самим: это
  уменьшает зависимости.
- Существующий код пайплайна работает с одной общей БД
  `data/state.sqlite`. Тики-evolve тоже пишут в неё (новые таблицы из
  Patch 38). `metrics`, `publications`, `qa_issues` — общие. Это
  означает, что snapshot должен фильтровать по тикеру.
- `app.paths.state_db` — путь к БД, читается из app.yaml (тот же что
  пайплайн).
- `edx update --ticker X --ticker Y --ticker Z` УЖЕ поддерживается
  существующим CLI (см. `_cmd_run` / `_cmd_update`). Для evolve
  используем `update`, не `run --full-reload`.

## Задачи

### 1. `src/edx/evolve/snapshot.py`

```python
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class TickerSnapshot:
    ticker: str
    publications_total: int
    publications_by_status: dict[str, int]   # {'discovered': N, 'written': M, ...}
    documents_total: int
    metrics_rows: int
    metrics_by_standard: dict[str, int]       # {'IFRS': k, 'RSBU': l, ...}
    qa_issues_count: int
    qa_issues_codes: dict[str, int]           # {'incomplete': 1, ...}
    last_publication_date: str | None         # max(publication_date) ИЛИ None

    def as_json_dict(self) -> dict:
        return asdict(self)


def snapshot_ticker(conn: sqlite3.Connection, ticker: str) -> TickerSnapshot:
    """Read all aggregates for a single ticker. Pure read; no transactions."""
    ...


def snapshot_batch(
    conn: sqlite3.Connection,
    tickers: list[str],
) -> dict[str, TickerSnapshot]:
    return {t: snapshot_ticker(conn, t) for t in tickers}
```

Запросы — короткие, индексы существуют (`idx_publications_ticker_date`,
`idx_metrics_ticker_date`). НЕ держим долгих транзакций.

### 2. `src/edx/evolve/runner.py`

```python
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PipelineRunResult:
    returncode: int
    duration_seconds: float
    stdout_tail: str            # last 4KB
    stderr_tail: str            # last 4KB
    log_path: Path              # path to the full pipeline.log


def run_pipeline_on_batch(
    tickers: list[str],
    *,
    config_dir: Path = Path("config-evolve"),
    log_path: Path,
    timeout_seconds: int = 30 * 60,
    extra_env: dict[str, str] | None = None,
) -> PipelineRunResult:
    """Run `.venv/bin/edx update --config-dir … --ticker A --ticker B --ticker C`.

    Captures stdout/stderr; full structured logs go to `log_path` via
    EDX_LOG_FILE env var. Hard-fails on subprocess timeout (raise).
    """
    ...
```

Должен:
- Использовать `sys.executable` корня venv → `Path(sys.executable).parent / "edx"`.
- Передать `EDX_LOG_FILE=str(log_path)` в env (см.
  `src/edx/logging_setup.py` — там уже есть поддержка переменной).
  Если поддержки нет — добавить минимальный handler по этой
  переменной (см. файл `logging_setup.py`).
- НЕ глотать `KeyboardInterrupt`.
- Хранить только хвосты stdout/stderr (4096 байт), полный лог идёт
  в файл.

### 3. `src/edx/evolve/verdict.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from edx.evolve.snapshot import TickerSnapshot

VerdictCode = Literal["ok", "neutral", "regression", "fail"]


@dataclass(frozen=True, slots=True)
class TickerVerdict:
    ticker: str
    code: VerdictCode
    metrics_delta: int          # after - before
    publications_written_delta: int
    qa_issues_delta: int
    notes: tuple[str, ...]


def compute_verdict(
    before: TickerSnapshot,
    after: TickerSnapshot,
    *,
    pipeline_returncode: int,
    min_metrics_for_ok: int = 1,
) -> TickerVerdict:
    """Compare two snapshots.

    Logic (priority order):
      - returncode != 0 AND metrics_delta == 0 → "fail"
      - metrics_after < metrics_before → "regression"
      - publications_written_delta < 0 → "regression"
        (existing 'written' rows became 'failed'/'skipped')
      - metrics_delta >= min_metrics_for_ok AND publications_written_delta >= 0
        → "ok"
      - otherwise → "neutral"

    `notes` is a small tuple of human-readable diffs for logs / bundle.
    """
    ...
```

`notes` примеры: `("metrics +5", "qa_issues +1: incomplete")`. Для
маленьких отчётов в `bundle/SUMMARY` и в logs.

### 4. `src/edx/evolve/tick.py`

```python
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from edx.config import AppSettings, load_all
from edx.evolve.csv_loader import CompanyRow, load_companies
from edx.evolve.picker import PickerInput, pick_next_batch
from edx.evolve.runner import run_pipeline_on_batch
from edx.evolve.snapshot import snapshot_batch
from edx.evolve.synth import write_evolve_config
from edx.evolve.verdict import compute_verdict
from edx.logging_setup import get_logger
from edx.storage import Database, EvolutionRepo


def run_one_tick(
    settings: AppSettings,
    *,
    csv_path: Path = Path("e-disclosure-companies.csv"),
    main_tickers_yaml: Path = Path("config/tickers.yaml"),
    cooldown_days: int = 7,
    batch_size: int = 3,
) -> int:
    """One tick of the evolve loop. Returns tick_id (or 0 if no candidates).

    This Patch 40 version:
      - picks batch
      - synth config-evolve
      - snap_before, run pipeline, snap_after
      - persists everything to evolution_ticks
      - on FAIL/REGRESSION simply ends and returns tick_id
      - DOES NOT call Claude Code yet

    Patch 42/43 will graft Claude Code + git_ops on top.
    """
    ...
```

Конкретный flow (commented в коде):

```
log = get_logger("edx.evolve.tick")
companies = load_companies(csv_path)
moex_ids = _read_moex_e_disclosure_ids(main_tickers_yaml)

db = Database(settings.app.paths.state_db); db.migrate()
with closing(db.connect()) as conn:
    repo = EvolutionRepo(db, conn)
    batch = pick_next_batch(PickerInput(
        companies=companies,
        moex_e_disclosure_ids=moex_ids,
        cooldown_days=cooldown_days,
        batch_size=batch_size,
    ), repo)
    if len(batch) < batch_size:
        log.info("evolve_no_candidates", picked=len(batch))
        return 0

    started_at = datetime.now(timezone.utc).isoformat()
    batch_json = json.dumps([{
        "company_id": c.company_id,
        "name": c.name,
        "ticker": c.synthetic_ticker,
        "profile": c.type,
    } for c in batch], ensure_ascii=False)
    tick_id = repo.create_tick(
        started_at=started_at, phase="baseline", batch_json=batch_json,
    )

    bundle_dir = Path(f"evolution/runs/{tick_id}")
    bundle_dir.mkdir(parents=True, exist_ok=True)
    write_evolve_config(batch, target_dir=Path("config-evolve"))

    snaps_before = snapshot_batch(conn, [c.synthetic_ticker for c in batch])
    (bundle_dir / "snap_before.json").write_text(
        json.dumps({k: v.as_json_dict() for k, v in snaps_before.items()},
                   ensure_ascii=False, indent=2)
    )

    log_path = bundle_dir / "pipeline.log"
    res = run_pipeline_on_batch(
        tickers=[c.synthetic_ticker for c in batch],
        config_dir=Path("config-evolve"),
        log_path=log_path,
    )
    snaps_after = snapshot_batch(conn, [c.synthetic_ticker for c in batch])
    (bundle_dir / "snap_after.json").write_text(...)

    verdicts = {
        c.synthetic_ticker: compute_verdict(
            snaps_before[c.synthetic_ticker],
            snaps_after[c.synthetic_ticker],
            pipeline_returncode=res.returncode,
        ) for c in batch
    }
    overall = _aggregate_verdict(verdicts)   # → "ok" | "fail" | "regression" | "neutral"

    repo.update_tick(
        tick_id,
        phase="done",
        verdict=overall,
        snaps_before_json=...,
        snaps_after_json=...,
        verdicts_json=json.dumps({t: asdict(v) for t, v in verdicts.items()}, ensure_ascii=False),
        finished_at=datetime.now(timezone.utc).isoformat(),
        bundle_path=str(bundle_dir),
        error_summary=None if overall == "ok" else f"per-company verdicts: {verdicts}",
    )
    log.info("evolve_tick_finished", tick_id=tick_id, verdict=overall)
    return tick_id
```

`_aggregate_verdict` — простая лесенка:
- все `ok` → `"ok"`
- любой `regression` → `"regression"`
- любой `fail` → `"fail"`
- иначе → `"neutral"`

`_read_moex_e_disclosure_ids` парсит yaml и возвращает set строк
(пропуская `REPLACE_ME`).

### 5. CLI: `src/edx/cli.py`

Добавить subparser группу `evolve` с одной subcommand `tick`:

```python
evolve_p = subparsers.add_parser(
    "evolve",
    help="Self-evolution loop subcommands.",
)
evolve_sub = evolve_p.add_subparsers(
    dest="evolve_command", required=True, metavar="subcommand"
)
evolve_tick_p = evolve_sub.add_parser(
    "tick",
    help="Run one self-evolution tick over a batch of 3 companies.",
)
evolve_tick_p.set_defaults(func=_cmd_evolve_tick)
```

`_cmd_evolve_tick` загружает settings и зовёт `run_one_tick(settings)`.

Возвращает `EXIT_OK` всегда — verdict пишется в БД, CLI exit-code не
зависит от исхода ETL-прогона (это design — systemd timer не должен
краснеть из-за «нормального» FAIL тика, только из-за crash).

### 6. Тесты `tests/evolve/`

- `tests/evolve/test_snapshot.py`:
  - `test_snapshot_empty_ticker`: тикер без публикаций → нули, None.
  - `test_snapshot_publication_aggregate`: insert вручную 3
    publication-rows + 1 metric → счётчики совпадают.
- `tests/evolve/test_verdict.py`:
  - таблица параметризованных кейсов: (returncode, before, after) → ожидаемый verdict.
  - покрыть все 4 ветки.
- `tests/evolve/test_runner_smoke.py`:
  - **monkeypatch** `subprocess.run` — runner не должен реально
    запускать пайплайн в юнит-тесте. Проверяем сборку argv,
    timeout, что `EDX_LOG_FILE` пробрасывается.
- `tests/evolve/test_tick_orchestration.py`:
  - **monkeypatch** `run_pipeline_on_batch` чтобы он только модифицировал
    БД (insert publications/metrics) — verdicts должны корректно
    собираться, тик пишется в `evolution_ticks`, файлы в bundle_dir
    появляются.
  - проверить случай «no candidates» → возвращает 0.

## Acceptance criteria

- `make lint` ✅ `make typecheck` ✅ `make test` ✅
- `.venv/bin/edx evolve tick --help` печатает справку.
- ВРУЧНУЮ на чистом репо: `.venv/bin/edx evolve tick` запускается,
  пишет тик в БД, делает реальный (или mock'ed) прогон пайплайна,
  завершает корректным verdict-кодом. Лог `evolution/runs/{N}/pipeline.log`
  содержит JSON-строки.
- В `evolution_ticks` появилась запись с `phase='done'`.

## Риски и инварианты

- Не запускаем `db.migrate()` дважды — пайплайн в subprocess мигрирует
  сам; родительский процесс делает `migrate()` один раз для своих
  Repo-вызовов.
- Если subprocess грохнулся — НЕ паникуем, просто `verdict='fail'`.
- Нельзя вызывать `run_one_tick` повторно в одном и том же процессе с
  открытым conn (есть риск SQLite locking) — закрывать conn после
  вызова, либо использовать `with closing` (как и сейчас).
- Логи параллельных тиков НЕ пересекаются — каждый тик пишет в свой
  `evolution/runs/{tick_id}/pipeline.log`.

## Что класть в MEMORY.md из этого патча

(Этот патч — инфраструктурный; entries в MEMORY.md появятся только
когда Claude Code начнёт работать в Patch 42+.)

Anti-pattern: «Не считайте verdict по суммарному кол-ву metrics в
state — только per-ticker, иначе один большой тикер маскирует регрессию
маленького».
