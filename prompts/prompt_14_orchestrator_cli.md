# Промпт 14. Оркестратор и CLI

## Цель
Связать все стадии в единый DAG, реализовать команды `edx update` (инкрементальный запуск, она же «кнопка обновить») и `edx run --full-reload` (полная переобработка с глубиной 3 года). Гарантировать идемпотентность и устойчивость к падениям отдельной публикации.

## Контекст из ТЗ
- Раздел 7: «оркестратор хранит DAG стадий и состояние выполнения в SQLite».
- Раздел 12.1: режимы запуска.
- Раздел 12.2: идемпотентность.
- Раздел 14: на падении публикации пайплайн не прерывается.

## Задачи
1. Создать `src/edx/orchestrator/`:
   - `dag.py`: декларативное описание DAG как списка стадий с входным/выходным статусом публикации:
     ```python
     STAGES = [
         StageStep("discoverer",   to_status="discovered",  scope="batch"),
         StageStep("downloader",   from_status="discovered",  to_status="downloaded",  scope="publication"),
         StageStep("unpacker",     from_status="downloaded",  to_status="unpacked",    scope="publication"),
         StageStep("classifier",   from_status="unpacked",    to_status="classified",  scope="publication"),
         StageStep("text_extract", from_status="classified",  to_status="extracted",   scope="publication"),
         StageStep("metric_extract", from_status="extracted", to_status="extracted",   scope="publication", when=lambda p: p.publication_type == "report"),
         StageStep("event_extract",  from_status="extracted", to_status="validated",   scope="publication", when=lambda p: p.publication_type == "event"),
         StageStep("validator",    from_status="extracted",   to_status="validated",   scope="publication"),
         StageStep("writer",       to_status="written",       scope="batch"),
         StageStep("replicator",   scope="batch"),
     ]
     ```
   - `runner.py`:
     - `Orchestrator.run(mode: Literal["update","full_reload"])`:
       - открывает запись в `runs`;
       - для `full_reload`:
         - перечитывает `tickers.yaml`;
         - сбрасывает `publications.status` → `discovered` для всех публикаций за последние 3 года (но **не удаляет** их и не повторно скачивает — Downloader/Unpacker увидят корректные хеши и пропустят локальные шаги; LLM-стадии проигнорируют кеш только если оператор отдельно очистил `_llm_cache`).
       - `update`-режим — стандартный инкрементальный поток.
       - вызывает `discoverer` (batch), затем для каждой публикации проходит per-publication стадии в порядке DAG. Per-publication — параллельно с лимитом из `app.yaml → orchestrator.publication_concurrency` (дефолт `4`).
       - На любой ошибке стадии:
         - логирует error с полным traceback;
         - переводит публикацию в `failed`, пишет `last_error`;
         - **не прерывает** обработку остальных публикаций (раздел 14).
       - после прохода — вызывает `writer` и `replicator` (даже если часть публикаций упала — экспортируем то, что есть).
       - закрывает запись в `runs` с финальным статусом: `succeeded` (все ОК), `partial` (есть failed), `failed` (упала batch-стадия Discoverer/Writer).
2. `src/edx/cli.py`:
   - `edx update` — `Orchestrator.run("update")`.
   - `edx run --full-reload` — `Orchestrator.run("full_reload")`.
   - `edx run --ticker SBER` — ограничение по тикеру (для отладки).
   - `edx status` — печатает последние 5 запусков из `runs` с краткой статистикой (количества по статусам публикаций).
   - все ранее добавленные изолированные команды (`discover`, `unpack`, `extract-metrics`, ...) остаются.
3. Сводка статистики в `runs.stats_json`:
   ```json
   {
     "publications_total": 42,
     "publications_by_status": {"written": 38, "failed": 2, "incomplete": 2},
     "metrics_rows": 190,
     "events_rows": 12,
     "duration_seconds": 873,
     "llm_calls": 40,
     "llm_fallback_calls": 1
   }
   ```

## Тесты, которые должны проходить
- Юнит-тесты DAG:
  - корректный порядок стадий;
  - `when`-фильтр пропускает event-публикации мимо `metric_extract` и наоборот.
- Юнит-тесты `Orchestrator.run` с замокированными сервисами стадий:
  - happy path с 3 публикациями (2 reports + 1 event) — все доходят до `written`;
  - падение в `metric_extract` для одной публикации не валит остальные; status `partial`;
  - падение `Discoverer` (batch-стадия) → `runs.status='failed'`, `writer/replicator` всё равно пытаются прогнать с тем, что уже есть в БД.
- Юнит-тест `--full-reload`:
  - публикации последних 3 лет → статус `discovered`; публикации старше 3 лет → не трогаются.

## Definition of Done
- `edx update` за один прогон проходит весь pipeline и завершается с записью в `runs`.
- Падение одной публикации не валит остальные.
- Любая стадия запускается изолированной CLI-командой.
- Никакой бизнес-логики в `cli.py` — только парсинг аргументов и вызов соответствующих сервисов.
