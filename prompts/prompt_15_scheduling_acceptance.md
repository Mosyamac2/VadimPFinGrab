# Промпт 15. Расписание, документация и приёмочные тесты

## Цель
Закрыть проект: установка, расписание (cron / systemd timer), сквозные приёмочные тесты, README с инструкциями оператора.

## Контекст из ТЗ
- Раздел 12: автоматический запуск раз в сутки через cron / systemd timer; время по умолчанию 04:00.
- Раздел 17, п.1: целевая ОС — Linux, без long-running процесса.
- Раздел 13: `.env` создаётся из `.env.example` вручную.
- Раздел 16, 18: чёткое описание границ scope.

## Задачи

### 1. Шаблоны планировщика
- `deploy/cron/edx.crontab`:
  ```
  # m h dom mon dow command
  0 4 * * * /usr/bin/env -i HOME=$HOME PATH=/usr/local/bin:/usr/bin:/bin /opt/edx/.venv/bin/edx update >> /opt/edx/logs/cron.log 2>&1
  ```
- `deploy/systemd/edx-update.service` (`Type=oneshot`, `ExecStart=/opt/edx/.venv/bin/edx update`, `WorkingDirectory=/opt/edx`).
- `deploy/systemd/edx-update.timer` (`OnCalendar=*-*-* 04:00:00`, `Persistent=true`).
- В обоих случаях время вынесено в комментарий с пояснением, что это дефолт из `app.yaml`.

### 2. README.md (полный)
Разделы:
1. Что это и для кого.
2. Системные требования (Linux, Python 3.11+, `unrar`, `tesseract-ocr` с rus+eng, `poppler-utils`).
3. Установка:
   ```
   git clone <repo>
   cd <repo>
   python3.11 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   apt-get install unrar tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils
   cp .env.example .env       # затем заполнить ключи
   edx config check
   edx auth google-drive       # один раз; вставить refresh_token в .env
   ```
4. Конфигурация (ссылки на каждый YAML с описанием).
5. Запуск:
   - Ручной: `edx update`.
   - Полная переобработка: `edx run --full-reload`.
   - Изолированно: `edx discover`, `edx unpack`, `edx extract-metrics`, `edx extract-events`, `edx validate`, `edx export-excel`.
6. Расписание: два варианта (cron / systemd timer), команды установки.
7. Где смотреть результат: `output/e-disclosure.xlsx`, ссылка на Google Drive (`edx status` показывает).
8. Логи и отладка: `logs/pipeline.log` (JSON), `data/state.sqlite` (через любой SQLite-вьювер), отдельная команда `edx status`.
9. Расширение:
   - Добавить эмитента → `config/tickers.yaml`.
   - Добавить показатель → `config/metrics.yaml`.
   - Добавить тип события → `config/event_types.yaml`.
10. Что НЕ входит в scope первой версии (раздел 18).
11. Перспективы: FastAPI-обёртка + iOS-клиент (раздел 15 ТЗ).

### 3. Приёмочные тесты (E2E)
Создать `tests/e2e/test_pipeline_acceptance.py`:
- Использовать **полностью замокированные** EDisclosureClient и LLMProvider (через DI), реальную SQLite, реальный `openpyxl`, замокированный Google Drive.
- Сценарий 1 «холодный backfill»:
  - 2 эмитента;
  - на каждый — по 1 IFRS-отчёту (machine-readable) и по 1 событию;
  - после `edx update`:
    - все 4 публикации в статусе `written`;
    - в `metrics` 10 строк (5 показателей × 2 эмитента);
    - в `events` 2 строки;
    - `output/e-disclosure.xlsx` существует и содержит ожидаемые листы и значения;
    - Google-Drive-replicator вызван с правильным локальным путём.
- Сценарий 2 «инкрементальный второй прогон»:
  - повторный `edx update` без новых публикаций → `runs.stats_json.publications_total = 0`, файл Excel не пересоздан с нуля (или пересоздан с тем же содержимым — допустимо);
  - LLM не вызывается (счётчик `llm_calls = 0`).
- Сценарий 3 «частичная неудача»:
  - один из 4 PDF подменён на «битый», вызывает падение в Text Extractor;
  - после `edx update`:
    - `publications.status = failed` для одной публикации, остальные `written`;
    - `runs.status = 'partial'`;
    - Excel содержит данные по успешным публикациям;
    - в `qa_issues` запись для упавшей публикации.

### 4. Финальные проверки
- `make lint typecheck test` зелёные.
- `pyproject.toml` имеет корректную версию (`0.1.0`), classifiers, описание.
- В `MANIFEST.in` (или `pyproject` package-data) включены `config/*.yaml`, `migrations/*.sql`.
- Установка из чистого venv (`pip install -e .`) даёт работающий `edx` без необходимости вручную ставить что-либо ещё, кроме системных пакетов из README.

## Тесты, которые должны проходить
- `pytest -q` — все юнит-тесты + E2E зелёные.
- `pytest tests/e2e -q` отдельно — все три сценария проходят.
- Опциональный системный smoke-тест (запускается оператором вручную):
  - на тестовом тикере с реальным e-disclosure ID `edx update` отрабатывает без ошибок и обновляет реальный Excel в реальном Google Drive.

## Definition of Done
- В репозитории присутствует всё необходимое для развёртывания на чистой Linux-машине: README + deploy-шаблоны + рабочий `pyproject.toml`.
- ТЗ закрыто по разделам 1–18 (ничто из «вне scope» не реализовано — никаких алёртов, REST API, мобильного приложения, динамического подтягивания индекса).
- Любые будущие правки (новый показатель, новый тип события, новый эмитент) делаются только конфигом.
