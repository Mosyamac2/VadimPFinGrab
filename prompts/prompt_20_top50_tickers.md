# Промпт 20. Реальный Top-50 tickers.yaml + helper для поиска e_disclosure_id

## Цель
Заменить заглушки `e_disclosure_id: REPLACE_ME` в `config/tickers.yaml` на настоящие идентификаторы эмитентов с e-disclosure.ru. Покрыть Топ-50 по капитализации Московской Биржи (или ближайший рабочий аналог) с полем `profile: bank | non_bank`. Дополнительно — поставить вспомогательный CLI-скрипт, который умеет искать `id` через поисковую страницу e-disclosure и сверяться вручную.

## Контекст
- `PLAN_e-disclosure_parser_v2.md` раздел 5, Patch 20.
- ТЗ §4 (источник списка эмитентов).
- Без этого патча Discoverer вообще не способен ходить на нужные карточки — `REPLACE_ME` блокирует `edx update` на старте.
- Зависит от Patch 19 (поле `profile` в `TickerSpec`). Не зависит от Patch 16/18.

## Задачи

### 1. Вспомогательный скрипт `tools/find_e_disclosure_ids.py`
- Standalone CLI, по аналогии с `tools/get_drive_token.py`.
- Запуск: `python tools/find_e_disclosure_ids.py --tickers SBER,GAZP,LKOH --out /tmp/ids.csv`.
- Алгоритм:
  - Читает `config/tickers.yaml`, берёт оттуда либо переданный список тикеров.
  - Для каждого тикера делает GET на страницу поиска эмитентов e-disclosure (например, `https://www.e-disclosure.ru/poisk-po-kompaniyam?query=<TICKER>` или соответствующий aspx-эндпоинт — точный URL фиксируется в коде, сейчас в плане указано общее направление).
  - Парсит результат: подбирает первую строку с релевантным сходством по названию (через `difflib.SequenceMatcher` против `name` из tickers.yaml) и извлекает числовой ID из ссылки `company.aspx?id=N`.
  - Печатает таблицу `ticker,name_in_search,suggested_id,confidence,url` в stdout, опционально CSV в `--out`.
  - Уважает rate-limit (1 RPS) и использует тот же `EDisclosureClient` или httpx с тем же UA/cookies.
- Скрипт **не пишет в `config/tickers.yaml` автоматически** — он только подсказывает оператору, который проверяет глазами и редактирует руками. Это сознательно: ID на e-disclosure для разных юр. лиц одной группы могут отличаться (например, головная компания vs специализированная дочка).

### 2. Реальный `config/tickers.yaml` Топ-50
- Сформировать список из 50 тикеров MOEX по последнему доступному рейтингу капитализации; источник можно зафиксировать в комментарии шапки YAML (например, индекс MOEX Total Return на дату коммита).
- На каждую запись: `ticker`, `name`, `e_disclosure_id` (целое число!), `profile` (`bank` или `non_bank`).
- Банки в обязательном порядке: **SBER, VTBR, BSPB, TCSG (T-Technologies), MBNK (МКБ), SVCB (Совкомбанк)** — `profile: bank`. Остальные — `non_bank`. Если в Топ-50 попадает что-то спорное (например, биржа MOEX), решение оператор фиксирует вручную и комментирует строку.
- ID **обязательно** проверены через `tools/find_e_disclosure_ids.py` или ручной поиск; нулевые/неочевидные оставлять с пометкой `# TODO id verification needed` нельзя — патч считается завершённым только когда все 50 валидируются скриптом валидации (см. п. 4).

### 3. Шаблонный файл
- `config/tickers.yaml.template` обновить так, чтобы пример показывал и банк, и корпорат:
  ```yaml
  tickers:
    - ticker: SBER
      name: ПАО Сбербанк
      e_disclosure_id: 3043
      profile: bank
    - ticker: LKOH
      name: ПАО ЛУКОЙЛ
      e_disclosure_id: 17
      profile: non_bank
  ```
- В шапке файла комментарием — ссылка на `tools/find_e_disclosure_ids.py`.

### 4. Скрипт валидации `tools/validate_tickers.py`
- Standalone CLI: `python tools/validate_tickers.py [--strict]`.
- Для каждого тикера проверяет **все 4 типа** (`type=2,3,4,5`) и сохраняет матрицу доступности.
- Категории результатов на (тикер, тип):
  - `OK` — 200 + есть `table.files-table` + строки в `tbody`
  - `EMPTY` — 200 + есть таблица, но `tbody` пустой (тип присутствует, но публикаций нет — например, новый эмитент)
  - `MISSING` — 200, но таблицы нет / 404 / 410 (эмитент **не публикует** этот тип через данный e_disclosure_id; **известный кейс — LKOH `id=17`, type=4**)
  - `ERROR` — 5xx, таймаут, парсинг упал
- Считается «пройденным», если хотя бы один из `type=3,4,5` имеет статус `OK` (без какого-либо источника метрик дальше делать нечего). Сам по себе `MISSING` для отдельного типа — **не ошибка**, а информация для оператора.
- Печатает таблицу `ticker | type=2 | type=3 | type=4 | type=5 | passes` и summary в конце.
- С `--strict` exit-code 1, только если у какого-то тикера ни один из type=3/4/5 не дал `OK`.
- Документировать в README раздел «Эмитенты с неполным набором типов»: указать **LKOH (id=17)** как канонический пример (МСФО недоступно по этому id; для извлечения МСФО Лукойла либо использовать другой e_disclosure_id, если найден, либо принять, что для LKOH работаем только с РСБУ — это отражается в `reporting_priority` через graceful fallback из Patch 19).

### 5. Тесты
- `test_tickers_config_loads_real_yaml`: загрузка реального `config/tickers.yaml` через Pydantic — проходит без ошибок, банков ≥6, не-банков ≥30, всего ровно 50.
- `test_tickers_config_profile_required` / `test_tickers_config_invalid_profile_rejected`.
- `test_validate_tickers_logic_with_lkoh_like_fixture`: юнит-тест на функцию валидации, которая принимает мок-клиент. Сценарий: тикер LKOH, type=4 → `MISSING`; type=3 → `OK`. Ожидаем `passes=True` (есть рабочий type=3) и в отчёте — пометка `MISSING` для type=4, без exit-code 1.
- `test_validate_tickers_logic_no_types_available`: все 4 типа `MISSING` → `passes=False`, в `--strict` exit-code 1.
- `test_find_e_disclosure_ids_smoke` (под `pytest.mark.integration`, помечен skip-by-default через env-var) — реальный запрос на сайт; в обычном CI пропускается.
- `test_validate_tickers_smoke` — аналогично, integration, off by default.

## Тесты, которые должны проходить
- Базовые юнит-тесты — зелёные.
- Запуск `python tools/validate_tickers.py` локально оператором — все 50 тикеров OK.
- `make lint typecheck test`.

## Definition of Done
- В `config/tickers.yaml` ровно 50 валидных эмитентов с реальными `e_disclosure_id`, у каждого выставлен `profile`.
- `tools/find_e_disclosure_ids.py` работает на любом подмножестве тикеров и возвращает осмысленные подсказки.
- `tools/validate_tickers.py` подтверждает доступность всех 50 карточек.
- `edx update` без флагов не падает на старте из-за `REPLACE_ME`.
- README содержит раздел «Как собрать tickers.yaml» с пошаговой инструкцией и упоминанием обоих скриптов.
