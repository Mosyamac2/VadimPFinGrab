# Промпт 38. Самоэволюция: миграция БД, модели, репозиторий, инициализация MEMORY.md

> Серия Patch 38–46 — реализация функциональности «самоэволюции» по
> плану [`PLAN_self_evolution.md`](../PLAN_self_evolution.md).
> Patch 38 — фундамент: расширение state.sqlite двумя таблицами и
> создание шаблона `evolution/MEMORY.md`. **Этот патч не меняет
> поведение пайплайна и безопасен к merge даже если последующие
> патчи отложатся.**

## Цель

1. Завести в `state.sqlite` две новые таблицы для трекинга тиков
   самоэволюции и skiplist'а компаний (см.
   [`PLAN_self_evolution.md` §5](../PLAN_self_evolution.md)).
2. Добавить Pydantic-модели `EvolutionTick` и `EvolutionSkiplist` в
   `src/edx/storage/models.py`.
3. Реализовать `EvolutionRepo` со стандартным набором CRUD-методов.
4. Завести шаблон `evolution/MEMORY.md` с пустым Index, пустым
   Patches log и заголовками Anti-patterns / Companies status.
5. Обновить `.gitignore`: `evolution/runs/` и
   `data/state.sqlite.tick*.bak` не идут в git.

## Контекст

- Существующая модель миграций: `src/edx/storage/migrations/000N_*.sql`
  (последняя — `0009_issuer_reporting_standard.sql`). `Database.migrate()`
  применяет их по порядку, каждая фиксируется в `schema_migrations`.
- Существующие репозитории — `src/edx/storage/repositories/*_repo.py`.
  Все они принимают `Database` и `sqlite3.Connection` в конструкторе,
  пишут структурированно, используют Pydantic модели как DTO.
- Pyproject у нас mypy strict, ruff E/F/W/I/B/UP/SIM. Любой новый
  модуль обязан быть полностью типизирован.

## Задачи

### 1. Миграция `src/edx/storage/migrations/0010_evolution.sql`

```sql
-- Patch 38: self-evolution loop bookkeeping.
--
-- evolution_ticks   — журнал тиков (один тик = batch из 3 компаний).
-- evolution_skiplist — компании, которые трижды подряд не получилось
--                     починить (Picker их пропускает до ручного reset).

CREATE TABLE evolution_ticks (
    tick_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at         TEXT    NOT NULL,
    finished_at        TEXT,
    phase              TEXT    NOT NULL CHECK(phase IN (
        'baseline','claude_code','verdict','done','failed')),
    verdict            TEXT             CHECK(verdict IN (
        'ok','neutral','regression','regression_tests','regression_canary',
        'fail','flaky','give_up','skipped_budget') OR verdict IS NULL),
    batch_json         TEXT    NOT NULL,        -- [{"company_id":..,"name":..,"ticker":..,"profile":..}, …]
    snaps_before_json  TEXT,                    -- {"EDX1210": {...}, ...}
    snaps_after_json   TEXT,
    verdicts_json      TEXT,                    -- {"EDX1210":"ok",...}
    claude_session     TEXT,
    claude_cost_usd    REAL,
    claude_turns       INTEGER,
    commit_sha         TEXT,
    bundle_path        TEXT,
    error_summary      TEXT
);

CREATE INDEX idx_evolve_started ON evolution_ticks(started_at);
CREATE INDEX idx_evolve_verdict ON evolution_ticks(verdict);

CREATE TABLE evolution_skiplist (
    company_id     TEXT PRIMARY KEY,
    reason         TEXT NOT NULL CHECK(reason IN ('give_up','manual_blacklist','moex_overlap')),
    failure_count  INTEGER NOT NULL DEFAULT 0,
    last_tick_id   INTEGER REFERENCES evolution_ticks(tick_id),
    updated_at     TEXT NOT NULL
);
```

### 2. Pydantic-модели в `src/edx/storage/models.py`

Добавить в конец файла:

```python
EvolutionPhase = Literal[
    "baseline", "claude_code", "verdict", "done", "failed"
]
EvolutionVerdict = Literal[
    "ok", "neutral", "regression", "regression_tests", "regression_canary",
    "fail", "flaky", "give_up", "skipped_budget"
]
SkiplistReason = Literal["give_up", "manual_blacklist", "moex_overlap"]


class EvolutionTick(BaseModel):
    """One self-evolution iteration over a batch of 3 companies."""

    tick_id: int
    started_at: str
    finished_at: str | None = None
    phase: EvolutionPhase
    verdict: EvolutionVerdict | None = None
    batch_json: str                    # raw JSON; parsed at the call-site
    snaps_before_json: str | None = None
    snaps_after_json: str | None = None
    verdicts_json: str | None = None
    claude_session: str | None = None
    claude_cost_usd: float | None = None
    claude_turns: int | None = None
    commit_sha: str | None = None
    bundle_path: str | None = None
    error_summary: str | None = None


class EvolutionSkiplistEntry(BaseModel):
    company_id: str
    reason: SkiplistReason
    failure_count: int = 0
    last_tick_id: int | None = None
    updated_at: str
```

### 3. Репозиторий `src/edx/storage/repositories/evolution_repo.py`

Public API (минимальный набор; больше методов вводим по мере
надобности — НЕ заходим в YAGNI):

```python
class EvolutionRepo:
    def __init__(self, db: Database, conn: sqlite3.Connection) -> None: ...

    # ticks ---------------------------------------------------------
    def create_tick(
        self,
        *,
        started_at: str,
        phase: EvolutionPhase,
        batch_json: str,
    ) -> int: ...

    def update_tick(
        self,
        tick_id: int,
        *,
        phase: EvolutionPhase | None = None,
        verdict: EvolutionVerdict | None = None,
        snaps_before_json: str | None = None,
        snaps_after_json: str | None = None,
        verdicts_json: str | None = None,
        claude_session: str | None = None,
        claude_cost_usd: float | None = None,
        claude_turns: int | None = None,
        commit_sha: str | None = None,
        bundle_path: str | None = None,
        error_summary: str | None = None,
        finished_at: str | None = None,
    ) -> None: ...

    def get_tick(self, tick_id: int) -> EvolutionTick | None: ...
    def latest_ticks(self, limit: int) -> list[EvolutionTick]: ...

    def daily_cost_usd(self, day: str) -> float:
        """Sum of claude_cost_usd for finished_at LIKE day || '%'."""

    # skiplist ------------------------------------------------------
    def get_skiplist(self) -> list[EvolutionSkiplistEntry]: ...
    def is_in_skiplist(self, company_id: str) -> bool: ...
    def bump_failure(self, company_id: str, last_tick_id: int) -> int:
        """Increment failure_count; if it hits 3 → set reason='give_up'.
        Returns the new failure_count."""

    def add_overlap(self, company_id: str) -> None:
        """Insert with reason='moex_overlap'. Idempotent."""

    def reset(self, company_id: str) -> bool:
        """Remove from skiplist. Returns True if a row was deleted."""
```

Все методы — **синхронные** (как остальные репо). DateTime — ISO8601
strings (см. `RunsRepo`). Не используйте `datetime.utcnow()` — у нас
везде `datetime.now(timezone.utc).isoformat()`.

### 4. Регистрация в `src/edx/storage/__init__.py`

Добавить `EvolutionRepo` и модели в `__all__`. Импорт — после
`RunsRepo` (алфавитный порядок не критичен, но соблюдаем стиль).

### 5. Шаблон `evolution/MEMORY.md`

Создать файл со следующим содержимым (никаких записей пока — только
заголовки):

```markdown
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

_no entries yet_

## Companies status (top 30 most recently touched)

| company_id | name | last_tick | verdict | metrics_count |
|---|---|---|---|---|
| _no entries yet_ | | | | |
```

### 6. Шаблон `evolution/runs/.gitkeep`

Пустой файл — чтобы каталог попал в репозиторий.

### 7. `.gitignore` дополнения

В корневой `.gitignore` добавить:

```gitignore
# Patch 38: self-evolution
evolution/runs/*
!evolution/runs/.gitkeep
data/state.sqlite.tick*.bak
.env.evolve
config-evolve/
```

### 8. Тесты

Создать `tests/storage/test_evolution_repo.py`:

- `test_migration_applied`: после `db.migrate()` в `schema_migrations`
  есть строка `0010_evolution`.
- `test_create_and_get_tick`: round-trip с минимальными полями.
- `test_update_tick_partial`: поля, которые не передали, не зануляются.
- `test_daily_cost_usd_sums`: создаём 2 тика с разными `finished_at`
  и `claude_cost_usd`, сумма сходится.
- `test_skiplist_bump_to_give_up`: 3 раза bump, на 3-й — статус
  `give_up`, 4-й вызов не растит счётчик дальше 3 (idempotent).
- `test_skiplist_add_overlap_idempotent`: дважды `add_overlap` не
  поднимает counter.
- `test_skiplist_reset`: удаляет запись, повторный reset возвращает False.

Pytest fixtures — наследуем существующие из `tests/storage/conftest.py`
(там уже есть `db` fixture с in-memory SQLite + миграциями).

## Acceptance criteria

- `make lint` ✅ `make typecheck` ✅ `make test` ✅
- `python -c "from edx.storage import EvolutionRepo; print('ok')"` печатает `ok`.
- `sqlite3 data/state.sqlite ".schema evolution_ticks"` показывает таблицу.
- `cat evolution/MEMORY.md` показывает шаблон.
- В `git diff --stat` 8–10 файлов, ни один — в `.env`, `.git`, `deploy/`.

## Риски и инварианты

- Миграция **только additive** — никаких ALTER на существующих таблицах.
  Откат тривиален: drop двух таблиц.
- Не добавляем новых внешних зависимостей.
- Не трогаем CLI — этот патч даёт ТОЛЬКО storage-инфраструктуру.
  CLI subcommand `edx evolve …` появится в Patch 40/43.
- `EvolutionRepo` не должен импортировать ничего из `src/edx/evolve/*` —
  это нижний слой.

## Из MEMORY.md (на случай повторного посещения этого патча в будущем)

- Если выкатываем drop этих таблиц — миграция-down пишется отдельно,
  но шаблон проекта DOWN-миграции не использует, мы катаемся forward-only.
- При расширении `phase`/`verdict` enum-ов: правим CHECK constraints
  через NEW таблицу + INSERT…SELECT (SQLite не умеет ALTER CHECK).
