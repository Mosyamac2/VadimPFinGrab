# Промпт 03. State-БД (SQLite)

## Цель
Описать схему `state.sqlite`, миграции и репозиторный слой. State-БД хранит и состояние пайплайна, и витрину одновременно (раздел 10.2 ТЗ).

## Контекст из ТЗ
- Раздел 10.2: таблицы `tickers`, `publications`, `documents`, `metrics`, `events`, `runs`.
- Раздел 7.2: оркестратор пишет состояние стадий в SQLite.
- Раздел 12.2: идемпотентность через хеш файла + статус.

## Задачи
1. Использовать чистый `sqlite3` из стандартной библиотеки + лёгкий слой, без ORM. Подключение через контекстный менеджер с `PRAGMA foreign_keys=ON`, `journal_mode=WAL`.
2. Создать `src/edx/storage/migrations/` с пронумерованными SQL-файлами:
   - `0001_init.sql`:
     - `tickers(ticker TEXT PRIMARY KEY, e_disclosure_id TEXT NOT NULL, inn TEXT, ogrn TEXT, name TEXT NOT NULL, added_at TEXT NOT NULL)`
     - `publications(publication_id TEXT PRIMARY KEY, ticker TEXT NOT NULL REFERENCES tickers(ticker), publication_type TEXT NOT NULL CHECK(publication_type IN ('report','event')), publication_date TEXT NOT NULL, source_url TEXT NOT NULL, file_hash TEXT, status TEXT NOT NULL CHECK(status IN ('discovered','downloaded','unpacked','classified','extracted','validated','written','failed','skipped')), last_error TEXT, discovered_at TEXT NOT NULL, updated_at TEXT NOT NULL)`
     - `documents(document_id INTEGER PRIMARY KEY AUTOINCREMENT, publication_id TEXT NOT NULL REFERENCES publications(publication_id) ON DELETE CASCADE, relative_path TEXT NOT NULL, mime_type TEXT, reporting_standard TEXT CHECK(reporting_standard IN ('IFRS','RSBU','OTHER') OR reporting_standard IS NULL), report_form TEXT, is_machine_readable INTEGER, page_count INTEGER, file_hash TEXT NOT NULL, UNIQUE(publication_id, relative_path))`
     - `metrics(metric_id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, reporting_date TEXT NOT NULL, period_type TEXT NOT NULL CHECK(period_type IN ('Q1','Q2','Q3','Q4','H1','H2','9M','FY')), reporting_standard TEXT NOT NULL CHECK(reporting_standard IN ('IFRS','RSBU')), metric_name TEXT NOT NULL, value REAL, currency TEXT NOT NULL, unit TEXT NOT NULL, source_document_id INTEGER REFERENCES documents(document_id), qa_warning TEXT, extracted_at TEXT NOT NULL, UNIQUE(ticker, reporting_date, period_type, reporting_standard, metric_name))`
     - `events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, event_date TEXT NOT NULL, publication_date TEXT NOT NULL, event_type TEXT NOT NULL, summary TEXT NOT NULL, key_params_json TEXT, source_url TEXT NOT NULL, source_publication_id TEXT REFERENCES publications(publication_id), extracted_at TEXT NOT NULL, UNIQUE(source_publication_id))`
     - `runs(run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','partial')), mode TEXT NOT NULL CHECK(mode IN ('update','full_reload')), stats_json TEXT, error_summary TEXT)`
     - индексы: `publications(ticker, publication_date)`, `metrics(ticker, reporting_date)`, `events(ticker, event_date)`.
3. Создать `src/edx/storage/db.py`:
   - `Database` с методами `connect()`, `migrate()`, `transaction()`.
   - `migrate()` ведёт служебную таблицу `schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT)`, применяет SQL-файлы из `migrations/` в алфавитном порядке.
4. Создать репозитории в `src/edx/storage/repositories/`:
   - `tickers_repo.py`: `upsert_from_config(entries)`, `list_active()`.
   - `publications_repo.py`: `upsert_discovered(...)`, `mark_status(publication_id, status, error=None)`, `get_by_id`, `latest_publication_date(ticker)`.
   - `documents_repo.py`: `add_documents(publication_id, docs)`, `update_classification(...)`, `list_for_publication`.
   - `metrics_repo.py`: `replace_for_publication(publication_id, rows)` (атомарно: удалить старые, вставить новые).
   - `events_repo.py`: `upsert_event(...)`.
   - `runs_repo.py`: `start_run(mode)`, `finish_run(run_id, status, stats, error_summary)`.
5. Все методы — синхронные, через явный `sqlite3.Connection`. Транзакция оборачивает каждое логическое действие.
6. На старте `edx update` синхронизировать содержимое `tickers.yaml` в таблицу `tickers`.

## Тесты, которые должны проходить
- `pytest tests/storage/` с in-memory SQLite (`:memory:`) и временным файлом:
  - применение миграций с нуля, idempotent повторное применение.
  - upsert тикеров: повторный вызов не плодит дубликатов и обновляет `name`.
  - переход публикации по статусам (`discovered` → `downloaded` → ... → `written`), невозможность вписать неизвестный статус (CHECK).
  - `replace_for_publication` атомарно перезаписывает метрики (если упало посередине — старые не удалены).
  - foreign keys работают (удаление публикации каскадно удаляет документы).

## Definition of Done
- `edx update` (без реальной работы по эмитентам) создаёт `data/state.sqlite`, прогоняет миграции, синхронизирует тикеров и пишет запись в `runs` с финальным статусом `succeeded`.
- В логе structlog видны структурированные записи о применённых миграциях.
- Никакая дальнейшая стадия не пишет SQL напрямую — только через репозитории.
