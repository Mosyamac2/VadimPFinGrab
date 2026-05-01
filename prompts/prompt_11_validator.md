# Промпт 11. Validator (sanity checks)

## Цель
Применить sanity-проверки к извлечённым показателям до записи в финальную витрину. Проверки **не блокируют запись**, а навешивают флаг `qa_warning`. Также формируется отдельный отчёт о проблемах извлечения для ручного разбора.

## Контекст из ТЗ
- Раздел 11.1: балансовое уравнение, знаки, YoY, валюты, единицы.
- Раздел 11.2: <50% метрик → `incomplete` в отдельный отчёт.
- Раздел 14: на падении публикация не блокирует пайплайн.

## Задачи
1. Создать `src/edx/stages/validator/`:
   - `rules.py` — чистые функции (без I/O), каждая возвращает `list[QAWarning]`:
     - `check_balance_equation(metrics_for_period) -> list[QAWarning]` — `Активы ≈ Капитал + Обязательства` ±0.5%. Если хотя бы один из трёх отсутствует — пропустить.
     - `check_signs(metrics_for_period)` — Активы и Выручка должны быть >= 0; Чистая прибыль и EBITDA — могут быть отрицательными.
     - `check_yoy(metrics_for_period, previous_period)` — флаг `suspicious_yoy`, если изменение > 10x по любому показателю.
     - `check_currency_consistency(metrics_for_period)` — все строки одной публикации в одной валюте.
     - `check_unit_consistency(metrics_for_period)` — единицы должны быть одинаковые в рамках одной публикации.
     - `check_completeness(extracted_count, requested_count, threshold)` — флаг `incomplete`, если < threshold.
     Каждое предупреждение — структура:
     ```python
     class QAWarning(BaseModel):
         code: str          # "balance_mismatch", "negative_revenue", "suspicious_yoy", "currency_mixed", "unit_mixed", "incomplete"
         message: str
         affected_metrics: list[str]
     ```
   - `service.py`:
     - `ValidatorService.run(publication)`:
       - читает строки `metrics` для публикации;
       - для YoY-проверки запрашивает предыдущий отчётный период того же эмитента из БД;
       - аккумулирует предупреждения, объединяет в JSON и проставляет в `metrics.qa_warning` (через миграцию `0004_qa_warnings.sql` — если ещё не сделано: колонка уже задана в `0001_init.sql`, дополнительной миграции не требуется);
       - если у публикации есть хоть одно warning или флаг `incomplete` — добавить запись в новую таблицу `qa_issues(issue_id, publication_id, ticker, code, message, created_at)` (миграция `0004_qa_issues.sql`).
       - переводит публикацию в статус `validated`.
2. Сформировать отчёт о проблемах извлечения (раздел 11.2):
   - `qa_issues` будет источником данных для отдельного листа Excel-витрины (на следующем шаге, в Writer).
3. CLI: `edx validate --publication-id <id>` — изолированный запуск.

## Тесты, которые должны проходить
- Чистые юнит-тесты на `rules.py`:
  - балансовое уравнение: точное соблюдение → нет warning; превышение 0.5% → warning `balance_mismatch`.
  - знаки: отрицательная выручка → warning `negative_revenue`.
  - YoY: рост в 11 раз → warning `suspicious_yoy`; в 5 раз — нет.
  - валюты/единицы: смешение → warning соответствующего кода.
  - completeness: 2 из 5 при threshold 0.5 → warning `incomplete`.
- Юнит-тест `service.py`:
  - на синтетических данных (3 публикации: чистая, с одним warning, с тремя warning) — корректное распределение по `metrics.qa_warning` и `qa_issues`.
- Юнит-тест: при отсутствии предыдущего периода YoY-проверка пропускается без ошибок.

## Definition of Done
- Валидация не блокирует запись — даже при всех warnings метрики остаются в `metrics`.
- В `qa_issues` появляется агрегированный список проблем для ручного разбора.
- Все правила покрыты юнит-тестами 1:1.
- Никаких внешних зависимостей у правил (можно прогонять без БД и сети).
