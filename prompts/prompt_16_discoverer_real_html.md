# Промпт 16. Перепроектирование Discoverer под реальный HTML e-disclosure.ru

## Цель
Полностью заменить синтетический парсер карточки эмитента на парсер реальной табличной разметки `/portal/files.aspx?id=X&type=Y`. Парсер должен извлекать тип документа, отчётный период (год + квартал/полугодие/9 мес/FY), дату размещения и прямую ссылку на ZIP-архив через `FileLoad.ashx?Fileid=N`.

## Контекст
- `PLAN_e-disclosure_parser_v2.md` разделы 2.1, 2.2, Patch 16.
- Реальные HTML-снимки сайта в `new_info/`:
  - SBER (банк, `id=3043`): `https___..._id=3043&type={1,2,3,4}.html` — 4 типа подряд.
  - LKOH (нефтегаз, `id=17`): `https___..._id=17&type=3.html` — РСБУ корпората с **глубокой историей** (61 строка, 2009–2026).
- ТЗ §2 (источник данных) и §7.1 п.1 (контракт стадии Discoverer).
- **Зависимости:** должен идти после Patch 17 (миграция схемы добавляет колонки `report_type_code`, `reporting_period_year`, `reporting_period_type`).
- **Принцип «не привязываться к одному эмитенту»:** парсер тестируется на материалах **минимум двух разных эмитентов** из разных секторов (банк + нефтегаз). Любой код, неявно завязанный на конкретный тикер (id, число строк, формат даты, путь по DOM), считается багом.

## Задачи

### 1. Реальные HTML-фикстуры (мульти-эмитент, обязательно)
- Создать каталог `tests/fixtures/edisclosure_real/`.
- Положить туда:
  - `sber_type_1.html`, `sber_type_2.html`, `sber_type_3.html`, `sber_type_4.html` — снимки SBER (`id=3043`).
  - `lkoh_type_3.html` — снимок LKOH РСБУ (`id=17`, type=3) — 61 строка с 2009 г., максимально длинный backlog.
- Все файлы — **view-source-снимки от Firefox**: настоящий HTML спрятан под двойной span-обёрткой (`<span id="line141"><span class="start-tag">div</span>…`). Парсер должен снимать обёртку программно — `HTMLParser(src).body.text()` возвращает декодированный исходник, и **дальше** уже идёт `HTMLParser(decoded).css("table.files-table")`. Не делать ручную нормализацию фикстур — иначе тест перестанет ловить реальную форму view-source.
- Старые synthetic-фикстуры `tests/fixtures/edisclosure/issuer_*.html` удалить.

**Edge-case с отсутствующим типом:** у LKOH (`id=17`) **`type=4` (МСФО) недоступен** на e-disclosure — головная компания публикует МСФО под другим юр. лицом или вовсе не на этом портале. У других мелких эмитентов может отсутствовать `type=5`. Это нормальный кейс, обработка — в п. 4.

### 2. Период-парсер `parser/period.py`
Чистый модуль без I/O. Функция `parse_reporting_period(value: str, *, type_code: int) -> ParsedPeriod | None`:
- `"2026, 3 месяца"` → `(year=2026, period_type="Q1")`
- `"2025, 6 месяцев"` → `(year=2025, period_type="H1")`
- `"2025, 9 месяцев"` → `(year=2025, period_type="9M")`
- `"2025"` (одним числом, type=2/3/4 годовой) → `(year=2025, period_type="FY")`
- `"2024"` для type=2 (там колонка «Отчётный год») → `(year=2024, period_type="FY")`
- Неопознанные форматы → `None` + warning через ParseResult.warnings.

**Покрытие:** проверка идёт на **полном объединённом наборе строк** из всех HTML-фикстур (SBER type=2/3/4 + LKOH type=3). Это автоматически даёт диапазон 2009 → 2026 и все 4 формы периода в нескольких комбинациях типов. Никаких ad-hoc «вот ещё один формат» в тесте — все ожидаемые `(year, period_type)` строятся **программно** из фикстур, чтобы добавление новой фикстуры в каталог автоматически покрывало парсер новыми кейсами.

Использовать словарь правил, чтобы добавление новых вариантов («1 квартал 2025», «I полугодие 2024») было правкой словаря, а не кода. Минимально предусмотреть alternation: `"3 месяца"` ≡ `"3 мес."` ≡ `"три месяца"`, и nbsp/тонкий пробел между числом и единицей.

### 3. Перезаписать `src/edx/stages/discoverer/parser.py`
- Вход: `parse_listing_page(html: str, *, base_url: str, ticker: str, type_code: int) -> ParseResult`.
- Извлечь `view-source` обёртку, если страница пришла оттуда (опционально, чтобы парсер съел и фикстуры, и реальный HTML).
- Найти `table.files-table > tbody > tr`.
- Для каждой строки кроме шапки (`th`) считать ячейки:
  - `td.row-number-cell` — пропустить.
  - `td.type-cell` — `report_type_label` (полная строка).
  - 3-я ячейка — «Отчётный период» / «Отчётный год» — через `period.parse_reporting_period`.
  - Предпоследняя `td.date-cell` — `publication_date` (формат `dd.mm.yyyy` → ISO).
  - `a.file-link[href]` + `data-fileid` — `source_url` и стабильный `publication_id` = `f"{ticker}-{type_code}-{fileid}"`.
- Контракт `DiscoveredPublication` расширить:
  ```python
  @dataclass(frozen=True)
  class DiscoveredPublication:
      publication_id: str
      publication_type: PublicationType        # 'report' для type=2/3/4/5; 'event' будет позже из другого URL
      report_type_code: int | None             # 2|3|4|5
      report_type_label: str | None            # текст из «Тип документа»
      reporting_period_year: int | None
      reporting_period_type: str | None        # 'Q1'|'H1'|'9M'|'FY'|None
      publication_date: str
      source_url: str
      title: str
  ```
- Малформированные строки (отсутствует ссылка / нечитаемая дата) → запись в `warnings`, строка пропускается.

### 4. Перезаписать `DiscovererService.run`
- Для каждого тикера обходить **четыре URL** последовательно с уважением rate-limit:
  ```
  /portal/files.aspx?id={e_disclosure_id}&type=2   # Годовые отчёты → ANNUAL
  /portal/files.aspx?id={e_disclosure_id}&type=3   # РСБУ
  /portal/files.aspx?id={e_disclosure_id}&type=4   # МСФО
  /portal/files.aspx?id={e_disclosure_id}&type=5   # Отчёт эмитента → ISSUER
  ```
- type=1 (уставные документы) НЕ обходить в этой стадии — это не периодическая отчётность.
- **Fail-soft на «нет такого типа»:** если ответ 200 OK, но `table.files-table` отсутствует / `tbody` пустой → лог `discoverer_no_publications_for_type` (level=info), переход к следующему типу. Если ответ 404 / 410 → тот же лог, level=info. Если 5xx или истёк retry-budget — лог level=warning и переход к следующему типу. Никакая комбинация (тикер, тип) не должна валить весь run — за один прогон собирается всё, что доступно. Это критично для эмитентов вроде LKOH, у которых нет `type=4`.
- События (`existing_facts`) пока не реализованы; оставить заглушку и комментарий «отдельная задача после Patch 16».
- Передавать в репозиторий через `PublicationsRepo.upsert_discovered` все новые поля (см. Patch 17).
- Фильтрация по дате `since[ticker]` остаётся как есть.

### 5. Маппинг type_code → reporting_standard
Детерминированный, без эвристик:
- `2 → "ANNUAL"` (Годовой отчёт — metadata-only, не источник метрик)
- `3 → "RSBU"`
- `4 → "IFRS"`
- `5 → "ISSUER"`

Эвристики из `classifier/heuristics.py` остаются как валидация (если в IFRS-документе нет ни одного маркера МСФО — warning), но не первоисточник классификации.

## Тесты, которые должны проходить
**Парсер на SBER (банк):**
- `test_parser_sber_type4_msfo`: 7 строк МСФО, корректные `(year, period_type)`, fileid из href, периоды покрывают «2026, 3 месяца» / «2025, 9 месяцев» / «2025, 6 месяцев» / «2024».
- `test_parser_sber_type3_rsbu`: РСБУ Сбер.
- `test_parser_sber_type2_annual_report`: колонка «Отчётный год», все строки `period_type='FY'`.

**Парсер на LKOH (нефтегаз) — multi-issuer:**
- `test_parser_lkoh_type3_rsbu`: 61 строка, 17 лет истории; самая старая публикация 2009-FY, самая свежая 2026-Q1; 4 формы периода представлены.
- `test_parser_handles_missing_type4`: моковый клиент возвращает HTML с пустым `tbody` или 404 — `parse_listing_page` отдаёт `ParseResult(publications=[], warnings=[])`, сервис логирует `discoverer_no_publications_for_type`, не валит run.

**Период-парсер (отдельно от Discoverer):**
- `test_period_parser_known_formats_from_fixtures`: ожидаемые `(year, period_type)` строятся **программно** из всех фикстур; добавление новой фикстуры автоматически расширяет покрытие.
- `test_period_parser_unknown_returns_none_with_warning`.
- `test_period_parser_unicode_spaces`: `"2025, 3 месяца"` → Q1 2025.

**Discoverer service:**
- `test_service_iterates_four_types_per_ticker`: мок-клиент возвращает 4 разных HTML; service делает 4 GET с правильными `type=` и **не** ходит на type=1.
- `test_service_continues_on_missing_type`: мок-клиент 404 на type=4; service продолжает с type=5 и type=2/3, итог содержит публикации из доступных типов.
- `test_service_passes_new_fields_to_repo`: мок-репо ловит вызовы с заполненными `report_type_code`, `reporting_period_year`, `reporting_period_type`.

## Definition of Done
- На фикстуре `sber_type_4.html` парсер возвращает 7 публикаций с правильными периодами и file_id.
- На фикстуре `sber_type_2.html` — все строки с `period_type='FY'`.
- На фикстуре `lkoh_type_3.html` парсер возвращает 61 публикацию, охватывающую 2009–2026, без потери ни одной строки и без ложных warnings (период-парсер должен покрыть все 4 формы).
- `DiscovererService.run` за один прогон обходит 4 URL на эмитент, fail-soft на 4xx/5xx и на пустые таблицы (continue по другим типам).
- Старые synthetic-фикстуры удалены, тесты, которые на них опирались, переписаны на реальные снимки.
- `make lint typecheck test` зелёные.
