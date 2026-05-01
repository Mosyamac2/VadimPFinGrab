# Промпт 09. Metric Extractor (LLM)

## Цель
Извлечь финансовые показатели из PDF/текстов отчётности через LLM, получив на выходе валидированный JSON: `metric_name → value → unit → currency → reporting_date → period_type → reporting_standard`. Применить приоритизацию МСФО > РСБУ.

## Контекст из ТЗ
- Раздел 5: список метрик, синонимы, формула, расширяемость через YAML.
- Раздел 5.1: МСФО — приоритет 1, РСБУ — приоритет 2 (только если МСФО отсутствует).
- Раздел 7.1, п.6: «строгая JSON-схема ответа».
- Раздел 11.2: <50% метрик → publication = `incomplete`.

## Задачи
1. Создать `src/edx/stages/metric_extractor/`:
   - `schema.py`:
     - построение JSON Schema из `MetricsConfig`. Топ-уровень — массив `extractions`, элемент:
       ```json
       {
         "reporting_date": "YYYY-MM-DD",
         "period_type": "Q1|Q2|Q3|Q4|H1|H2|9M|FY",
         "reporting_standard": "IFRS|RSBU",
         "currency": "RUB|USD|EUR|...",
         "unit": "ones|thousands|millions|billions",
         "metrics": {
            "<canonical_name>": {"value": <number|null>, "source_quote": "<строка из документа>"}
         }
       }
       ```
       `metrics` — это `additionalProperties: false` с явным списком ключей из `MetricsConfig`.
   - `prompts.py`:
     - `build_system_prompt(metrics_config)` — на русском, кратко: задача, формат ответа, синонимы (брать из конфига), запрет на придумывание чисел, инструкция возвращать `null`, если показатель не найден, и обязательный `source_quote` для каждой непустой метрики.
     - Поддержать prompt caching (большая статичная часть в начале system-промпта).
   - `service.py`:
     - `MetricExtractorService.run(publication)`:
       - выбирает «лучший» документ для извлечения по приоритету `IFRS > RSBU` (раздел 5.1):
         - если есть IFRS-документы — берём их (могут быть несколько форм; объединить тексты с разделителями);
         - иначе — RSBU.
         - если ни тех, ни других — пометить публикацию `skipped` со статусом и не вызывать LLM.
       - формирует `LLMRequest`:
         - **если provider primary поддерживает PDF-input и документ machine-readable** — передаёт PDF-байты;
         - иначе — собирает `user_text` из `text_extract_path` JSON (с указанием `--- page N ---` разделителей).
       - вызывает LLM-цепочку, парсит JSON, валидирует Pydantic-моделью `MetricExtractionResult`.
       - нормализует значения: переводит в базовую единицу из `metrics.yaml` (тысячи/миллионы → штуки), сохраняя оригинальную единицу в логе.
       - применяет формулы из `metrics.yaml` для отсутствующих показателей (например, EBITDA = Net Income + D&A + Interest + Tax — если все компоненты есть, и формула задана в YAML).
       - помечает `is_primary_for_period` у документа, который реально использовался (см. колонку из промпта 06).
       - пишет результат в `metrics` через `metrics_repo.replace_for_publication`.
       - переводит публикацию в статус `extracted` (если ещё не там) или `validated` после Validator.
2. Покрытие <50% (раздел 11.2): после прогона посчитать `extracted_metric_count / requested_metric_count`. Если меньше порога из `app.yaml → validator.completeness_threshold` (дефолт 0.5) — статус публикации `incomplete`. Стадия Validator из следующего промпта проставит флаг `qa_warning="incomplete"` на каждой строке (или сделать здесь — не критично, главное чтобы информация дошла до отчёта о проблемах).
3. На стороне CLI: `edx extract-metrics --publication-id <id>` — изолированный запуск.

## Тесты, которые должны проходить
- Юнит-тест построения JSON Schema из тестового `MetricsConfig` — фиксированный snapshot.
- Юнит-тест `prompt_builder` — стабильность вывода (snapshot-тест).
- Юнит-тест приоритизации:
  - публикация с IFRS+RSBU → выбран IFRS;
  - только RSBU → выбран RSBU;
  - ни того, ни другого → publication.status = `skipped`, LLM не вызван.
- Юнит-тест нормализации:
  - значение `1500` с `unit=thousands` → в БД `1_500_000`.
- Юнит-тест с замокированным `LLMProvider` (возвращает фиксированный JSON):
  - корректно записывает строки в `metrics_repo`;
  - повторный прогон → repo вызывается с той же транзакцией, дубликатов нет (UNIQUE);
  - если LLM вернул невалидный JSON — поднимается ошибка, публикация → `failed`, остальные публикации не страдают (раздел 14: «на падении не прерывается»).
- Юнит-тест completeness: имитировать ответ с заполнением 2 из 5 метрик → публикация помечена `incomplete`.

## Definition of Done
- Стадия не делает прямых HTTP-вызовов — только через `LLMProvider`.
- Список метрик меняется правкой YAML, без изменений Python-кода стадии.
- Никаких хардкоженных синонимов или валют в коде.
