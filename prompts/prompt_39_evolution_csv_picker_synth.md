# Промпт 39. Самоэволюция: загрузка CSV, Picker батча, Synth конфига

> Зависимости: Patch 38 (миграция БД, `EvolutionRepo`).
> Этот патч добавляет три pure-функциональных модуля без побочных
> эффектов в state.sqlite — поэтому полностью покрывается юнит-тестами,
> без e2e.

## Цель

1. Загружать `e-disclosure-companies.csv` в типизированную модель
   `CompanyRow` с обязательной колонкой `type` (`bank | non_bank`).
2. **Picker** выбирает БАТЧ из 3 компаний согласно правилам:
   - skiplist (give_up | manual_blacklist | moex_overlap) пропускается;
   - never_attempted имеют наивысший приоритет;
   - `failed_recoverable` (failure_count < 3) — следующий приоритет;
   - cooldown: компания, у которой последняя попытка `verdict='ok'`
     закончилась < `cooldown_days` назад, не выбирается;
   - порядок детерминирован (по company_id ASC внутри приоритета),
     чтобы тесты были воспроизводимы.
3. **Synth** пишет `config-evolve/tickers.yaml` с тремя записями и
   `config-evolve/app.yaml` с override `mode.backfill_years: 1`.
   Остальные конфиги (`metrics.yaml`, `event_types.yaml`, `ocr.yaml`,
   `llm.yaml`) — symlink на `config/`.

## Контекст

- CSV (актуальный):
  ```
  id,name,type
  1210,Банк ВТБ (ПАО),bank
  38588,ПАО «иэк холдинг,non_bank
  …
  ```
- `config/tickers.yaml` уже использует Pydantic-модель `TickerEntry`
  (см. `src/edx/config/tickers_config.py`). Поля: `ticker`, `name`,
  `e_disclosure_id`, `profile`, опц. `inn/ogrn/priority_override`.
- `config/app.yaml` — Pydantic `AppSettings` (`src/edx/config/app_config.py`).
  Поле `mode.backfill_years: int`.
- Модель `EvolutionSkiplistEntry` уже есть (Patch 38).

## Задачи

### 1. Каталог `src/edx/evolve/`

Новый top-level подпакет:

```
src/edx/evolve/
  __init__.py
  csv_loader.py
  picker.py
  synth.py
```

`__init__.py` экспортирует `CompanyRow`, `pick_next_batch`,
`write_evolve_config`. Импорт `from edx.evolve import …` должен
работать без бокового эффекта.

### 2. `src/edx/evolve/csv_loader.py`

```python
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CompanyType = Literal["bank", "non_bank"]


@dataclass(frozen=True, slots=True)
class CompanyRow:
    company_id: str         # numeric, but kept str (e-disclosure ids are TEXT)
    name: str
    type: CompanyType

    @property
    def synthetic_ticker(self) -> str:
        return f"EDX{self.company_id}"


def load_companies(
    path: Path = Path("e-disclosure-companies.csv"),
) -> list[CompanyRow]:
    """Load all companies from the input CSV.

    Required header: ``id,name,type``. Type values must be ``bank`` or
    ``non_bank`` (case-insensitive on read; normalised to lowercase).

    Raises ``ValueError`` on:
      - missing file
      - missing/extra columns
      - empty id, empty name
      - type ∉ {bank, non_bank}
    """
    ...
```

Никаких эвристик. Никакой нормализации имени (берём как в CSV).

### 3. `src/edx/evolve/picker.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from edx.evolve.csv_loader import CompanyRow
from edx.storage.repositories.evolution_repo import EvolutionRepo


@dataclass(frozen=True, slots=True)
class PickerInput:
    companies: list[CompanyRow]
    moex_e_disclosure_ids: set[str]   # из config/tickers.yaml
    cooldown_days: int = 7
    batch_size: int = 3
    today_iso: str = ""               # для тестов; "" → current UTC


def pick_next_batch(
    inp: PickerInput,
    repo: EvolutionRepo,
) -> list[CompanyRow]:
    """Return up to ``batch_size`` companies ordered by priority.

    Priority key (lower = higher priority):
      0. Never attempted (no row in evolution_ticks for this company_id).
      1. Failed recoverable (latest verdict ∈ {fail, regression, regression_*}
         AND not in skiplist AND failure_count < 3).
      2. OK but cooldown expired (latest verdict='ok' AND finished_at older
         than cooldown_days).

    Companies excluded:
      - in skiplist with reason ∈ {give_up, manual_blacklist}.
      - e_disclosure_id ∈ moex_e_disclosure_ids → silently add to skiplist
        with reason='moex_overlap', then exclude.
      - latest verdict='ok' AND cooldown not expired.

    Tiebreaker: company_id ASC (deterministic for tests).
    """
    ...
```

Реализация должна быть pure — единственная сторонняя зависимость это
`EvolutionRepo` (read-only методы + `add_overlap`). Никакого file I/O.

### 4. `src/edx/evolve/synth.py`

```python
from __future__ import annotations

from pathlib import Path

from edx.evolve.csv_loader import CompanyRow


def write_evolve_config(
    batch: list[CompanyRow],
    target_dir: Path = Path("config-evolve"),
    base_dir: Path = Path("config"),
    backfill_years_override: int = 1,
) -> None:
    """Materialise a per-tick config-dir.

    Behaviour:
      - target_dir must exist; if not, create it.
      - Write target_dir/tickers.yaml with 3 batch entries.
        Format mirrors config/tickers.yaml (commented header optional).
      - Write target_dir/app.yaml = base_dir/app.yaml with the single
        field mode.backfill_years overridden to `backfill_years_override`.
      - For metrics.yaml, event_types.yaml, ocr.yaml, llm.yaml:
        ensure target_dir/{name} is a symlink → base_dir/{name}.
        (relative symlink so it survives moves; recreate if stale.)
      - Idempotent: safe to call repeatedly.

    Does NOT mutate state.sqlite.
    """
    ...
```

YAML-генерация: используем `yaml.safe_dump(..., allow_unicode=True,
default_flow_style=False, sort_keys=False)`. Структура tickers.yaml:

```yaml
# Auto-generated by edx evolve tick. DO NOT edit manually —
# overwritten on every tick.

tickers:
  - ticker: EDX1210
    name: Банк ВТБ (ПАО)
    e_disclosure_id: "1210"
    profile: bank
  - ticker: EDX38588
    name: ПАО «иэк холдинг
    e_disclosure_id: "38588"
    profile: non_bank
  - ticker: EDX2541
    name: АО "Карельский окатыш"
    e_disclosure_id: "2541"
    profile: non_bank
```

`app.yaml` override — копия base + изменённое поле. Реализуйте через
`yaml.safe_load` → mutate dict → `yaml.safe_dump`. Не используйте
ruamel/jinja — лишняя зависимость.

### 5. Тесты `tests/evolve/`

Создаём подкаталог. Нужны:

- `tests/evolve/__init__.py` (пустой).
- `tests/evolve/conftest.py` — fixture `tmp_csv` (tmp_path с минимальным
  CSV), fixture `evolution_repo` (in-memory).
- `tests/evolve/test_csv_loader.py`:
  - валидный CSV → 3 записи правильного типа;
  - отсутствие колонки `type` → `ValueError("missing column 'type'")`;
  - пустая строка `id` → `ValueError`;
  - `type='Bank'` (uppercase) → нормализуется в `bank`;
  - `type='unknown'` → `ValueError`;
  - synthetic_ticker = `EDX1210`.
- `tests/evolve/test_picker.py`:
  - `test_picker_picks_3_never_attempted`: репо пустое, 5 компаний в CSV
    → возвращает первые 3 по company_id ASC.
  - `test_picker_skips_moex_overlap`: id=3043 (SBER) в moex_overlap →
    автоматически добавляется в skiplist с reason='moex_overlap';
    выбираются следующие 3.
  - `test_picker_skips_give_up`: компания в skiplist с reason='give_up' —
    пропускается.
  - `test_picker_priority_failed_over_ok_cooldown`: смешанный сценарий
    с явными `started_at`/`finished_at`/`verdict` записями в репо.
  - `test_picker_cooldown`: последняя успешная попытка 6 дней назад при
    `cooldown_days=7` → не выбирается; 8 дней назад → выбирается.
  - `test_picker_returns_empty_when_no_candidates`: skiplist на всё.
  - `test_picker_deterministic_order`: 100× вызов на одном и том же
    репо/CSV возвращает одинаковую тройку.
- `tests/evolve/test_synth.py`:
  - `test_synth_writes_tickers_yaml`: round-trip yaml.safe_load даёт 3
    записи с верными ticker/profile.
  - `test_synth_writes_app_yaml_with_override`: load → backfill_years==1,
    остальные поля равны исходному `config/app.yaml`.
  - `test_synth_creates_symlinks`: остальные 4 yaml — симлинки.
  - `test_synth_idempotent`: вызов 2 раза подряд не падает и финальный
    результат совпадает.

## Acceptance criteria

- `make lint` ✅ `make typecheck` ✅ `make test` ✅
- `python -c "from edx.evolve import load_companies, pick_next_batch, write_evolve_config; print('ok')"` → `ok`.
- При запуске `python -c "from edx.evolve import load_companies; print(len(load_companies()))"` (с реальным CSV в корне) — печатает 125.
- В `git diff --stat` 7–10 файлов; всё в `src/edx/evolve/`, `tests/evolve/`.

## Риски и инварианты

- НЕ изменяем `src/edx/config/tickers_config.py` — Picker/Synth не
  должны зависеть от Pydantic-загрузки конфига (избегаем циклов).
- Synth НЕ запускает пайплайн — только формирует config.
- Symlink стратегия: если на каком-то FS симлинки запрещены (NTFS под
  WSL), фоллбек на `shutil.copy` с пометкой в логе. На Linux VPS —
  всегда симлинки.
- `config-evolve/` есть в `.gitignore` (Patch 38). НИКАКИХ commit'ов
  его содержимого.

## Что класть в MEMORY.md из этого патча

- failure_class: «picker_picks_skiplisted_company» — не возникает в Patch 39
  (нет агента); но как guardrail указать в anti-patterns.
- Anti-pattern: «Не добавляйте `os.urandom` или `random.shuffle` в picker —
  потеряется детерминизм тестов».
