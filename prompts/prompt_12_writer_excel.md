# Промпт 12. Writer: SQLite-витрина и Excel

## Цель
Сформировать выходной Excel-файл `output/e-disclosure.xlsx` из `state.sqlite` с тремя листами: `metrics`, `events`, `meta`, плюс служебный лист `qa_issues` для контроля качества.

## Контекст из ТЗ
- Раздел 7.1, п.9 и раздел 10.3: листы и колонки.
- Раздел 11.2: «отдельный отчёт о проблемах извлечения для ручного разбора».
- Раздел 18: только последняя успешная по каждой публикации (никаких исторических версий).

## Задачи
1. Добавить зависимость `openpyxl`.
2. Создать `src/edx/stages/writer/`:
   - `excel.py`:
     - `class ExcelWriter` с методом `write(out_path: Path, snapshot: WitrineSnapshot)`.
     - `WitrineSnapshot` агрегирует данные из БД (см. ниже).
     - Лист `metrics`: колонки `ticker | reporting_date | period_type | reporting_standard | metric_name | value | currency | unit | qa_warning | source_publication_url`. Заголовки — жирным, фриз верхней строки, автоматическая ширина по содержимому, форматирование чисел («Регулярное» с разделителями тысяч, без дробной части для целочисленных).
     - Лист `events`: колонки `ticker | event_date | publication_date | event_type | summary | key_params_json | source_url`. Сортировка по `event_date desc`.
     - Лист `meta`: ключ-значение — `last_updated_at`, `pipeline_version` (из `edx.__version__`), `tickers_count`, `metrics_rows`, `events_rows`, `incomplete_publications`, `failed_publications`.
     - Лист `qa_issues`: колонки `ticker | publication_id | code | message | created_at`. Сортировка по `created_at desc`.
   - `service.py`:
     - `WriterService.run() -> Path`:
       - читает данные из репозиториев (новый метод в `metrics_repo.list_all_for_export()`, в `events_repo.list_all_for_export()`, в `qa_issues_repo.list_all()`);
       - формирует `WitrineSnapshot`;
       - вызывает `ExcelWriter.write` с атомарной записью: писать в `output/e-disclosure.xlsx.tmp`, потом `os.replace` на финальный путь;
       - возвращает путь к финальному файлу.
3. Расширить статусы публикации: после успешной записи листов перевести публикацию в `written`. Идемпотентность — повторный прогон не падает; писатель работает в режиме «снапшот всей витрины», а не «инкрементальная аппенда».
4. CLI: `edx export-excel` — изолированно.

## Тесты, которые должны проходить
- Юнит-тест `ExcelWriter` на синтетическом `WitrineSnapshot`:
  - после `write` файл существует и читается обратно `openpyxl` с теми же значениями;
  - проверить структуру всех 4 листов и заголовки колонок;
  - числовое форматирование применено (проверка по `cell.number_format`).
- Юнит-тест атомарности: симулировать падение между `tmp` и `replace` — финальный файл не повреждён (если `tmp` не записался полностью, прежняя версия остаётся).
- Юнит-тест `service.py`:
  - на временном SQLite заполнить 2 публикации × 5 метрик + 1 событие → Excel содержит 10 строк metrics, 1 строку events, корректный `meta`.

## Definition of Done
- После прогона стадии в `output/e-disclosure.xlsx` лежит актуальная витрина из БД.
- Никакой логики «инкрементальной правки» Excel — только полная перегенерация (раздел 10.4: «Excel перезаписывается»).
- Файл открывается в Excel/Numbers/LibreOffice без warning.
