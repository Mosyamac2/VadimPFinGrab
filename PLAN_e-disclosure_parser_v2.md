# План доработки проекта e-disclosure extractor

## Course-correction patches к существующей кодовой базе VadimPFinGrab

**Версия:** 2.0 (заменяет план 1.0 после анализа существующего кода)
**Дата:** 2026-05-01
**База:** `VadimPFinGrab-master` (15 промпт-итераций, ~640 КБ src, ~450 КБ tests)
**Статус:** на согласование

---

## 0. Что изменилось по сравнению с планом 1.0

План 1.0 предполагал реализацию с нуля по основному ТЗ. Реально проект уже построен Claude Code строго по этому ТЗ (15 последовательных промпт-этапов) — но **до того, как мы провели разведку реальной структуры e-disclosure.ru и реального содержимого архивов**. В результате костяк правильный, а несколько ключевых модулей построены вокруг **синтетических предположений**, которые расходятся с фактами с сайта.

Этот документ заменяет план 1.0. Вместо "что построить" в нём — **"что починить и где"**, с привязкой каждого патча к конкретным файлам репозитория.

---

## 1. Что в проекте уже хорошо и менять не нужно

Чтобы было видно, какой объём кода **остаётся как есть**:

- **Модульная архитектура стадий** (`src/edx/stages/{discoverer,downloader,unpacker,classifier,text_extractor,metric_extractor,event_extractor,validator,writer}/`) — точно соответствует тому, что мы заложили в основном ТЗ.
- **HTTP-клиент** (`src/edx/http/client.py`): `httpx` async + `aiolimiter` для rate-limit + `tenacity` retry + `RobotsCache` + `User-Agent` через конфиг, а также cookies-injection для обхода ServicePipe/Cloudflare. **Уже учитывает антибот-защиту** — это закрывает мою озабоченность из плана 1.0.
- **LLM-провайдер с fallback** (`src/edx/providers/llm/`): абстракция `LLMProvider`, прямой Anthropic с PDF-input как primary, OpenRouter как fallback, кеш по хешу контента. Реализовано ровно так, как мы хотели.
- **Unpacker** (`src/edx/stages/unpacker/service.py`): поддерживает и ZIP, и RAR, защита от path traversal, лимит на размер распакованного. ZIP-ветка работает прямо сейчас, RAR-ветка — мёртвый код, можно оставить (издержки нулевые).
- **State-БД с миграциями** (`src/edx/storage/migrations/0001..0006.sql`): шесть миграций, идемпотентная схема. Расширения схемы делаются добавлением новой миграции, без переписывания.
- **Validator** (`src/edx/stages/validator/`): балансовое уравнение, знаки, YoY-аномалии, согласованность валют — реализовано в `rules.py`.
- **Writer**: Excel-витрина с листами `metrics`, `events`, `meta`, `qa_issues` + репликация на Google Drive с update (а не create) и опциональными снапшотами в `archive/`. Соответствует разделам 10.3–10.4 ТЗ.
- **Promo-driven методология** (`prompts/01..15`): проект построен сериями фокусных промптов с зелёными тестами как gating-критерием. **Этот же подход используется в этом плане для патчей** — каждая правка ниже формулируется как отдельный focused-prompt.

---

## 2. Что в проекте расходится с реальностью и требует правок

Все находки получены из сравнения кода с (а) реальными скриншотами портала, (б) приложенными архивами, (в) разведкой URL-структуры из плана 1.0.

### 2.1. КРИТИЧНО — `discoverer/parser.py` парсит несуществующую разметку

**Файл:** `src/edx/stages/discoverer/parser.py`

В коде селекторы:
```python
section.publications-section[data-section=reports|events]
.publication-row → .publication-date + a.publication-link
```

Это **полностью синтетическая разметка**, появившаяся из тестовых фикстур `tests/fixtures/edisclosure/issuer_*.html`. Реальный e-disclosure использует **табличную HTML-разметку** (`<table>` с колонками "№", "Тип документа", "Отчётный период", "Дата утверждения", "Дата размещения", "Файл"). Парсер на живом сайте вернёт пустой результат — все строки уйдут в `warnings: "no publications-section found"`.

**Кроме того:** парсер делит публикации на `report` / `event`, но не извлекает **тип отчёта** (МСФО / РСБУ / Годовой / Отчёт эмитента) и не извлекает **отчётный период** (год + период_тип). А оба эти поля критичны для нашего пайплайна — на них завязаны: выбор источника по приоритету в `metric_extractor`, нормализация даты в `MetricInput.reporting_date`, корректное разнесение по периодам в Excel.

### 2.2. КРИТИЧНО — `discoverer/service.py` обходит не те URL

**Файл:** `src/edx/stages/discoverer/service.py`, строка 124–125

```python
def _issuer_card_path(self, e_disclosure_id: str) -> str:
    return f"/portal/company.aspx?id={e_disclosure_id}"
```

Это **сводная карточка** эмитента — там есть пара последних новостей и ссылки в подразделы, но **нет полного списка отчётности**. Вся отчётность лежит на отдельных URL по типам:

```
/portal/files.aspx?id={e_disclosure_id}&type=2   # Годовые
/portal/files.aspx?id={e_disclosure_id}&type=3   # РСБУ
/portal/files.aspx?id={e_disclosure_id}&type=4   # МСФО (консолидированная)
/portal/files.aspx?id={e_disclosure_id}&type=5   # Отчёты эмитента
```

Discoverer должен обходить **четыре URL на каждого эмитента**, а не один. Сообщения о существенных фактах живут на ещё одном URL (карточка `company.aspx` + лента `events.aspx`) — это отдельная логика, обсуждается в патче 5.

### 2.3. ВАЖНО — схема БД не хранит report_type_code и reporting_period

**Файл:** `src/edx/storage/migrations/0001_init.sql`, таблица `publications`

```sql
publication_type TEXT NOT NULL CHECK(publication_type IN ('report','event'))
publication_date TEXT NOT NULL    -- дата размещения
```

Две дыры:
- Нет колонки **`report_type_code`** (2/3/4/5) — а от неё зависит выбор экстрактора и приоритизация источников. Сейчас тип отчётности (`IFRS`/`RSBU`/`OTHER`) определяется эвристикой по тексту первых 3 страниц в `classifier/heuristics.py` — это работает примерно так же, как угадывание по обложке. Тип нужно знать **детерминированно** из URL, а не угадывать из текста.
- Нет колонок **`reporting_period_year`** + **`reporting_period_type`** — а они приходят из листинга (колонка "Отчётный период" в HTML-таблице) и нужны для записи в `MetricInput.reporting_date` + `period_type`.

### 2.4. ВАЖНО — `is_machine_readable` оценивается на уровне документа, а не страницы

**Файлы:** `src/edx/stages/classifier/pdf_inspector.py`, `src/edx/stages/classifier/service.py`

Текущая логика:
```python
def is_machine_readable(path, *, min_text_chars=400, pages=3) -> bool:
    text = extract_first_pages_text(path, pages=pages)
    return len(text.strip()) >= min_text_chars
```

То есть смотрим первые 3 страницы и ставим **бинарный флаг на весь документ**.

Что я наблюдал на приложенном архиве `RPBU_9m2025_pdf.zip` (РСБУ Сбера за 9м2025):

```
Страница 1: 334 символов текста    ← аудиторское заключение, текст
Страница 2: 2562 символов
Страница 3: 5805 символов
Страница 4: 6220 символов
Страница 5: 1 символ                ← начинаются СКАНЫ официальных форм
Страница 6: 1 символ
...
Страница 17: 1 символ
```

При `min_text_chars=400` и `first_pages_to_inspect=3` документ будет помечен как machine-readable — **OCR на него никогда не запустится, и финансовые данные с форм 0409806/0409807 в витрину не попадут**. Это тихий ложноотрицательный результат: пайплайн пройдёт зелёным, метрики либо пустые, либо угаданы LLM-ом из аудиторской преамбулы.

### 2.5. УЛУЧШЕНИЕ — нет профилей "банк / небанк"

**Файлы:** `config/tickers.yaml`, `config/metrics.yaml`, `src/edx/stages/metric_extractor/prompts.py`

Текущий `metrics.yaml` декларирует одинаковый набор показателей (revenue, ebitda, net_income, total_assets, total_debt) для всех эмитентов. Для банков в МСФО-отчётности **revenue / EBITDA / total_debt в классическом виде отсутствуют** — там аналогами выступают `net_interest_income`, `net_fee_income`, и понятия "финансового долга" отдельно от обычных пассивов нет. Пример с Сбером ниже:

```
МСФО Сбер 1Q2026 (содержание):
  Промежуточный консолидированный отчет о финансовом положении
  Промежуточный консолидированный отчет о прибылях и убытках
  Промежуточный консолидированный отчет о совокупном доходе
  Промежуточный консолидированный отчет об изменениях в составе собственных средств
  Промежуточный консолидированный отчет о движении денежных средств
```

LLM в текущей конфигурации получит prompt вида "извлеки выручку, EBITDA, чистую прибыль, активы, долг" и для Сбера сможет надёжно извлечь только **net_income** и **total_assets**. Остальные три поля будут `null` — а пороговый `completeness_threshold=0.5` на наборе из 5 метрик пометит публикацию `is_incomplete=1` *по причине неподходящего конфига показателей*, а не из-за реальной неполноты данных.

### 2.6. УЛУЧШЕНИЕ — Отчёт эмитента (type=5) не используется как источник, хотя самый AI-friendly

**Файл:** `src/edx/stages/metric_extractor/service.py`, метод `_pick_documents`

Сейчас приоритизация:
```python
for standard in self.metrics_config.reporting_priority:   # ['IFRS','RSBU']
    chosen = [d for d in documents if d.reporting_standard == standard]
    if chosen:
        return chosen, standard
```

Если документ — Отчёт эмитента, классификатор по эвристике пометит его `OTHER` (там нет прямых маркеров "МСФО"/"РСБУ" в первых страницах — там оглавление и шаблонная преамбула), и `_pick_documents` его пропустит. Между тем именно в ОЭ есть раздел **1.4 "Основные финансовые показатели"** — структурированная таблица с готовыми расчётными KPI за два периода (текущий и сравнительный), специально предназначенная для ручного и автоматического чтения. Я это видел в `SberOEH_6m2025_PredpCB_pdf.zip`:

```
№   Наименование показателя        Методика расчёта        6 мес 2025    6 мес 2024
1   Чистые процентные доходы ...   ...                     1 309,4       1 243,1
2   Чистая процентная маржа        ...                     6,1           5,9
...
```

Этот источник нужно использовать **как третий приоритет после МСФО и РСБУ** (или как первый — для эмитентов, у которых МСФО публикуется с большой задержкой).

### 2.7. КОСМЕТИКА — README акцентирует RAR

**Файл:** `README.md`, секция "Системные требования"

```
| Системные пакеты | unrar, tesseract-ocr, ... | RAR + OCR |
```

Это вводит оператора в заблуждение — на e-disclosure архивы только ZIP. `unrar` остаётся опциональным fallback'ом, но на первом плане должен быть `tesseract-ocr` + `poppler-utils`.

### 2.8. ДАННЫЕ — `tickers.yaml` пустой

Все три заглушки имеют `e_disclosure_id: REPLACE_ME`. Это **не баг кода**, а пропущенный шаг инициализации. Нужно добавить как часть P0-патчей — без него пайплайн не запустится ни на одном эмитенте.

---

## 3. План патчей: серия promo-driven доработок

Проект использует методологию из `prompts/README.md` — фокусные промпты, последовательные ветки `step-NN-<short-name>`, gating через зелёные тесты. Применяем тот же стиль к патчам. Нумерация продолжает существующую (предыдущая последняя была `prompt_15`).

### Patch 16 — Перепроектирование Discoverer под реальную HTML-структуру [P0, ~2 дня]

**Файлы:** `src/edx/stages/discoverer/parser.py`, `src/edx/stages/discoverer/service.py`, `tests/fixtures/edisclosure/*`, `tests/stages/discoverer/test_parser.py`, `tests/stages/discoverer/test_service.py`

**Действия:**
1. **Заменить fixture-файлы на реальные снапшоты сайта.** Один раз пройти ручкой `https://www.e-disclosure.ru/portal/files.aspx?id=3043&type={2,3,4,5}` (Сбер) и `https://www.e-disclosure.ru/portal/files.aspx?id=934&type={2,3,4,5}` (Газпром, для разнообразия), сохранить полный HTML каждой страницы. Положить в `tests/fixtures/edisclosure_real/` под именами `sber_type_4.html` и т.д. Эти фикстуры — **золотой стандарт**, по ним перестраивается парсер.
2. **Переписать `parser.py`** с табличными селекторами. Целевой контракт расширяется:
   ```python
   @dataclass(frozen=True)
   class DiscoveredPublication:
       publication_id: str
       publication_type: PublicationType         # report | event (как было)
       report_type_code: int | None              # NEW: 2/3/4/5 (None для event)
       report_type_label: str | None             # NEW: "МСФО" | "РСБУ" | "Annual" | "Issuer"
       reporting_period_year: int | None         # NEW: 2024
       reporting_period_type: str | None         # NEW: "FY" | "9M" | "H1" | "Q1"
       publication_date: str                     # ISO (как было — дата размещения)
       source_url: str                           # ссылка на ZIP-архив
       title: str
   ```
3. **Парсер `parse_listing_page(html, *, base_url, ticker, type_code) -> ParseResult`.** Принимает заранее известный type_code (его передаёт сервис, из URL), что снимает с парсера задачу детектировать тип. Парсит таблицу и поле "Отчётный период" через отдельный модуль `period_parser.py` со словарём правил (см. план 1.0, раздел 3.3).
4. **Переписать `DiscovererService.run`** так, чтобы для каждого тикера обходить **четыре URL** `files.aspx?id=X&type={2,3,4,5}` последовательно, с уважением rate-limit. Sсборный список публикаций уходит на upsert в БД.
5. **Тесты:**
   - `test_parser_extracts_table_rows_from_real_fixture` — 4 теста по типу.
   - `test_parser_handles_period_label_variants` — таблица из 12 строк "2024 / 2025, 9 месяцев / 2025, 6 месяцев / 1 квартал 2025" → ожидаемые `(year, period_type)`.
   - `test_service_iterates_all_four_types` — мок клиента возвращает 4 разных HTML, проверяем что service делает 4 запроса с правильными `type=`.

**Замечание про fixture-генерацию:** так как сайт под антибот-защитой, для первой ручной выгрузки фикстур используется браузер + "Save Page As → HTML Only". Это разовая операция, в CI не нужна.

### Patch 17 — Расширение схемы publications новыми полями [P0, ~0.5 дня]

**Файлы:** `src/edx/storage/migrations/0007_publications_period.sql` (новая), `src/edx/storage/models.py`, `src/edx/storage/repositories/publications.py`

**Действия:**
1. Добавить миграцию `0007_publications_period.sql`:
   ```sql
   ALTER TABLE publications ADD COLUMN report_type_code INTEGER;
   ALTER TABLE publications ADD COLUMN report_type_label TEXT;
   ALTER TABLE publications ADD COLUMN reporting_period_year INTEGER;
   ALTER TABLE publications ADD COLUMN reporting_period_type TEXT
       CHECK(reporting_period_type IN ('FY','9M','H1','Q1','Q2','Q3','Q4','H2') OR reporting_period_type IS NULL);
   CREATE INDEX idx_publications_period
       ON publications(ticker, reporting_period_year, reporting_period_type);
   ```
2. Обновить `PublicationRow` (добавить поля) и `PublicationsRepo.upsert_discovered` (принимать новые параметры, писать их).
3. Обновить тесты репозитория `tests/storage/test_publications_repo.py`.

Все колонки nullable — старые записи остаются валидными, миграция не теряет данные (это в стиле существующих 0002–0006).

### Patch 18 — Per-page классификация text/scan + обновление Text Extractor [P1, ~1.5 дня]

**Файлы:** `src/edx/stages/classifier/pdf_inspector.py`, `src/edx/stages/classifier/service.py`, `src/edx/storage/migrations/0008_document_pages.sql`, `src/edx/stages/text_extractor/service.py`

**Действия:**
1. Добавить в `pdf_inspector.py` функцию:
   ```python
   def classify_pages(path: Path, *, min_text_chars_per_page: int = 80) -> PageClassification:
       """Возвращает per-page разбиение: какие страницы текстовые, какие — сканы."""
       # для каждой страницы: page.get_text("text") → длина → text/scan
       # дополнительно: если на странице мало текста, но много image area → scan
   ```
2. Сохранять список номеров сканированных страниц в новой таблице (или JSON-колонкой документа):
   ```sql
   ALTER TABLE documents ADD COLUMN scanned_page_numbers TEXT;  -- JSON-массив [5,6,7,...]
   ALTER TABLE documents ADD COLUMN text_page_numbers TEXT;     -- JSON-массив [1,2,3,4]
   ```
3. В `text_extractor/service.py` — изменить логику с "если документ machine-readable → весь через native, иначе весь через OCR" на "пропустить text_pages через native, scanned_pages через OCR, склеить результат с пометкой источника на каждой странице".
4. Тесты:
   - `test_classify_pages_mixed_document` — на минимальном PDF из 3 страниц (1 текст + 2 скана) проверяем правильное разбиение.
   - `test_text_extractor_routes_pages_correctly` — мок OCR-провайдера, проверяем что вызывается ровно для нужных страниц.

**Правда жизни:** кейс смешанного документа массово встречается у банковских РСБУ (аудиторское заключение текстом + сканированные формы), поэтому без этого патча для банков пайплайн будет тихо терять данные.

### Patch 19 — Профили банк/небанк в metrics.yaml + tickers.yaml [P1, ~1 день]

**Файлы:** `config/metrics.yaml`, `config/tickers.yaml`, `src/edx/config/metrics_config.py`, `src/edx/config/tickers_config.py`, `src/edx/stages/metric_extractor/prompts.py`, `src/edx/stages/metric_extractor/schema.py`

**Действия:**
1. **`config/metrics.yaml`** — переструктурировать в:
   ```yaml
   profiles:
     non_bank:
       reporting_priority: [IFRS, ISSUER, RSBU]   # ОЭ как третий приоритет
       metrics:
         - canonical_name: revenue
           ...
     bank:
       reporting_priority: [IFRS, ISSUER, RSBU]
       metrics:
         - canonical_name: net_interest_income
           synonyms_ifrs: ["Net interest income", "Чистые процентные доходы"]
         - canonical_name: net_fee_income
           ...
         - canonical_name: net_income
         - canonical_name: total_assets
         - canonical_name: total_equity
   ```
2. **`config/tickers.yaml`** — добавить поле `profile: bank | non_bank` на каждую запись. Заполнить для Top-50 (из них банков ~5–7: SBER, VTBR, BSPB, TCSG, MBNK, SVCB).
3. **Pydantic-схема `MetricsConfig`** — заменить плоский список метрик на словарь по профилям.
4. **Metric Extractor** — выбирает профиль по `ticker.profile`, строит промпт и JSON-схему динамически.
5. **Тесты:**
   - `test_metrics_config_loads_profiles` — два профиля грузятся, разные наборы метрик.
   - `test_extractor_picks_bank_profile_for_sber` — по тикеру SBER собирается промпт с net_interest_income, без revenue/EBITDA.

### Patch 20 — Top-50 тикеров MOEX → e_disclosure_id [P0, данные ~1 день]

**Файлы:** `config/tickers.yaml`, новый `scripts/build_tickers.py`

**Действия:**
1. Однократный скрипт `scripts/build_tickers.py`, на вход — список тикеров MOEX (можно из MOEX ISS API или вручную из текущего IMOEX-состава), на выход — заготовка YAML с заполненными `name` / `inn`. `e_disclosure_id` — пометка `MANUAL` для случаев, где автомэппинг через ИНН не сработал.
2. Резолвинг `e_disclosure_id`: для каждой компании пробуем поиск через `https://www.e-disclosure.ru/poisk-po-kompaniyam` (можно по ИНН если поддерживается, иначе по названию), берём первый результат, парсим из URL карточки `company.aspx?id=X`.
3. Финальная вычитка списка — руками. Перед коммитом.
4. Опционально: добавить в `tickers.yaml` поле `moex_ticker` отдельно от `ticker`, если они отличаются (редко, но бывает после ребрендинга).

**Не имеет тестов** — это data-задача, не код. Проверка: `edx config check` после заполнения.

### Patch 21 — Подключение ОЭ (type=5) как источника метрик [P2, ~1 день]

**Файлы:** `src/edx/stages/classifier/heuristics.py`, `src/edx/stages/metric_extractor/service.py`, `src/edx/stages/metric_extractor/prompts.py`

**Действия:**
1. **Расширить `ReportingStandardWithOther`** до `IFRS | RSBU | ISSUER | OTHER`. Источник классификации — детерминированный, из `publications.report_type_code`: `4 → IFRS`, `3 → RSBU`, `5 → ISSUER`, `2 → ANNUAL` (использовать ANNUAL как metadata-only, не как metric source). Эвристики из `heuristics.py` остаются как валидация (если в МСФО-документе нет ни одного маркера МСФО — это сигнал, что документ положили не в тот раздел).
2. **`metric_extractor/prompts.py`** — добавить отдельный промпт-блок для `ISSUER`:
   ```
   Ты получаешь раздел 1.4 "Основные финансовые показатели" Отчёта эмитента
   за период {period}. Извлеки значения, сопоставив наименования из раздела
   методики расчёта с каноническими именами {profile.metrics}.
   Раздел 1.4 содержит только KPI — не пытайся извлекать показатели,
   которых нет в таблице.
   ```
3. **`metric_extractor/service.py`** — `_pick_documents` использует `reporting_priority` из профиля (см. Patch 19): `[IFRS, ISSUER, RSBU]`.
4. **Перед отправкой ОЭ в LLM** — урезать PDF до раздела 1.4. Якоря для поиска: "Основные финансовые показатели" в TOC, нумерация раздела `1.4.1` / `1.4.2` / `1.4.3` / `1.4.4` / `1.4.5`. Для банков релевантен 1.4.3, для небанков — 1.4.1 + 1.4.2. Отправлять в LLM только эти 5–10 страниц (вместо всего 60-страничного ОЭ).
5. **Тесты:** mock LLM, прогон на сжатой версии `SberОЭ_6м2025_ПредпЦБ.pdf` (можно подложить как fixture), проверяем что net_interest_income = 1309.4 извлечён.

### Patch 22 — Косметика README + .env.example + USER_GUIDE [P3, ~0.5 дня]

**Файлы:** `README.md`, `USER_GUIDE.md`, `.env.example`

**Действия:**
1. В таблице "Системные требования" сделать `unrar` опциональным (комментарий: "только для исторических архивов до 20XX, опционально"), на первый план — `tesseract-ocr-rus` + `poppler-utils`.
2. В разделе "Установка" — пример `apt install` без `unrar`.
3. В `USER_GUIDE.md` — добавить раздел "Как заполнить tickers.yaml" со ссылкой на `scripts/build_tickers.py` и пошаговой инструкцией про профили `bank` / `non_bank`.

---

## 4. Порядок исполнения и зависимости

```
Patch 17 (миграция)  ──┐
                       ├──> Patch 16 (parser)  ──> Patch 21 (ОЭ как источник)
Patch 20 (тикеры)    ──┘                                ↑
                                                        │
Patch 18 (per-page)  ──> Patch 19 (профили) ────────────┘
                                                        │
                                          Patch 22 (косметика, в любом месте после остальных)
```

**Рекомендуемый порядок:** 17 → 20 → 16 → 18 → 19 → 21 → 22.

Patch 17 первый, потому что он изолирован (только миграция + типы) и снимает блокер "куда писать новые поля". Patch 20 параллельно — это data-задача, может идти фоном. Patch 16 — самый объёмный и центральный, после него уже видно, что система действительно тянет реальные данные с сайта. Дальше по убыванию критичности.

**Оценка суммарно:** ~7 рабочих дней против ~17 дней на реализацию с нуля по плану 1.0. Существующий код экономит **больше половины работы**.

---

## 5. Что нужно проверить экспериментально перед стартом патчей

Это пересечение с разделом 11 плана 1.0, но скорректированное под факт существования проекта:

1. **Проверить, что текущий пайплайн запускается** на пустой `tickers.yaml`:
   ```bash
   git clone <repo> && cd edx
   python3.11 -m venv .venv && source .venv/bin/activate
   pip install -e .
   cp .env.example .env  # положить ключи
   edx config check
   ```
   Ожидаем: красивая ошибка про `e_disclosure_id: REPLACE_ME`. Если падает раньше или иначе — есть скрытые проблемы окружения, которые нужно решить до патчей.

2. **Прогнать `pytest -q`** на чистом репозитории — все ~50 тестов должны быть зелёные. Если что-то красное, разбираем перед патчами (это сигнал, что в репозитории есть deltas, о которых я не знаю).

3. **Сделать "dry-run" обхода e-disclosure:** написать одноразовый скрипт `scripts/scrape_one_url.py`, который через текущий `EDisclosureClient` запрашивает `https://www.e-disclosure.ru/portal/files.aspx?id=3043&type=4` и сохраняет HTML в `/tmp/sber_type4.html`. Если возвращается 200 OK с реальной разметкой — антибот-защита нам не препятствует. Если 403 / челлендж-страница — нужно решить вопрос с заголовками / cookies до Patch 16, иначе фикстуры взять не получится.

4. **Прогнать прямой Anthropic API на МСФО:** скрипт, который читает `MSFO_Rus_3m2026_pdf.zip` → `pdf_bytes` → отправляет в `LLMProvider.complete` с текущей JSON-схемой (revenue/EBITDA/net_income/total_assets/total_debt). Ожидаем: net_income и total_assets извлечены корректно, revenue/EBITDA/total_debt — `null` (это норма, не баг). Если **net_income и total_assets тоже null** — проблема в промпте или схеме, и Patch 19 (профили) становится более срочным.

Эти четыре проверки занимают ~2–3 часа суммарно и страхуют от запуска патчей вслепую.

---

## 6. Что НЕ меняем

Чтобы не было искушения раздуть scope:

- **LLM-провайдер и его кеш** — работает, не трогаем.
- **HTTP rate-limit / retry / robots** — работает, не трогаем (если только Patch 16 не выявит, что нужны кастомные заголовки на конкретные URL — тогда добавляется через существующий `discoverer.user_agent` / `discoverer.cookies` без правки кода).
- **Validator** — корректен, его правила перекрывают наши потребности (балансовое уравнение, знаки, YoY-аномалии).
- **Writer Excel + Google Drive** — работает, не трогаем.
- **Orchestrator + CLI + cron-инструкция** — работают.
- **Поддержка RAR в Unpacker** — мёртвый код, но безвредный. Удалять не надо (затраты на правку и регрессию тестов > выгода).
- **Эвристики `detect_reporting_standard` / `detect_report_form`** — оставляем как **fallback** на случай, если из URL тип не понятен (например, в "годовых отчётах" иногда лежат МСФО-приложения — эвристика поможет переклассифицировать).

---

## 7. Что остаётся вне scope патчей (как и в плане 1.0)

- Парсинг отчётов до 2020 года (другая структура ежеквартального отчёта).
- ESG-данные из ГО.
- Полный CFS со всеми строками cash flow.
- Кросс-валидация со СКРИН / Интерфакс-API.
- Аффилированные лица (`type=6`) и эмиссионные документы (`type=7`).
- Мобильное приложение iOS и REST API.

---

## 8. Следующие действия

1. Подтвердить план патчей и порядок (раздел 4).
2. Выполнить экспериментальные проверки (раздел 5) — 2–3 часа.
3. По их итогам — старт с **Patch 17** (миграция) и **Patch 20** (тикеры) параллельно.
4. Дальше Patch 16 как ключевой технический блок, остальные — по убыванию критичности.

После всех патчей P0/P1 (16, 17, 18, 19, 20) проект **впервые** способен тянуть реальные данные с e-disclosure и заполнять Excel. P2 (21) и P3 (22) — улучшения качества и UX.
