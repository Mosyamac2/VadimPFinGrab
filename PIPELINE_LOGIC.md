# Логика парсинга, OCR и LLM в edx

Документ описывает, что именно делает пайплайн `edx` от запроса
`edx update` до строки в Excel-витрине: какие стадии срабатывают,
где включается OCR, какой контекст уходит в LLM и что приходит
обратно. Назначение — дать оператору и разработчику единое место,
куда смотреть, чтобы понять «почему вот это число попало (или не
попало) в `e-disclosure.xlsx`».

> Версия документа: 2026-05-02 (после Patch 28, branch `master` →
> commit `7af2ec3`). Высокоуровневые требования — в
> [`TZ_e-disclosure_extractor.md`](TZ_e-disclosure_extractor.md);
> архитектурный план v2 — в
> [`PLAN_e-disclosure_parser_v2.md`](PLAN_e-disclosure_parser_v2.md).

---

## 0. Общая схема

```
┌─ Discoverer ──────────────► HTML /portal/files.aspx?id=X&type={2,3,4,5}
│  (Playwright Chromium)
│
├─ Downloader ─────────────► PDF/HTML/ZIP под data/raw/{TICKER}/{PUB_ID}/
│
├─ Unpacker ──────────────► распаковка ZIP/RAR (магия по сигнатурам)
│
├─ Classifier (PDF) ──────► per-page text/scan + reporting_standard
│      │                    из URL type_code (Patch 25) или текста
│      └── классификация хранится в documents.pages_classification (JSON)
│
├─ Text Extractor ────────► JSON-файлы под data/processed/{T}/{PUB}/{DOC_ID}.json
│      ├── native: pymupdf + pdfplumber таблицы
│      ├── ocr (Tesseract rus+eng): pdf2image @ 400 DPI + PSM 6 + retry
│      └── hybrid: native страницы + OCR только для scan-страниц
│
├─ Metric Extractor (LLM) ► одна вызовка LLM на публикацию
│      ├── профиль: bank | non_bank (config/tickers.yaml)
│      ├── источник: IFRS | RSBU | ISSUER (приоритет в metrics.yaml)
│      ├── контекст: PDF (Anthropic) или text-extract из Text Extractor
│      └── ответ: JSON со списком extractions[] по периодам
│
├─ Event Extractor (LLM) ► по одному вызову LLM на сообщение о событии
│      ├── контекст: текст HTML/PDF из Downloader/Text Extractor
│      └── ответ: JSON с event_type, event_date, summary, key_params
│
├─ Validator ─────────────► sanity-checks, qa_warning, qa_issues
│
└─ Writer / Replicator ──► output/e-disclosure.xlsx + Google Drive
```

Все стадии имеют свой подмодуль в `src/edx/stages/`, общие contract'ы
в `src/edx/storage/` (SQLite). Между стадиями нет «памяти» сверх
SQLite + файловой системы — каждая стадия читает state.sqlite,
делает свою работу, апдейтит state.

---

## 1. Discoverer — что ищем на портале

**Файл:** `src/edx/stages/discoverer/service.py`,
`src/edx/stages/discoverer/parser.py`,
`src/edx/stages/discoverer/period.py`.

Discoverer обходит для каждого тикера четыре URL:

| URL | Стандарт | Как используется |
|---|---|---|
| `/portal/files.aspx?id=X&type=2` | Annual report | metadata-only; не источник метрик |
| `/portal/files.aspx?id=X&type=3` | RSBU (РПБУ) | приоритет 2 |
| `/portal/files.aspx?id=X&type=4` | IFRS (МСФО) | приоритет 1 |
| `/portal/files.aspx?id=X&type=5` | Issuer report (отчёт эмитента) | приоритет 3 (fallback) |

`type=1` (статутные документы) не сканируется — это устав, проспекты
эмиссии и т.д., не нужно для KPI.

HTML-парсер ищет `table.files-table` (Patch 16). Каждая строка таблицы
становится `DiscoveredPublication` с полями:

- `source_url` — прямая ссылка на страницу публикации (используется
  как `metrics.source_publication_url` в Excel);
- `report_type_code` — `2 | 3 | 4 | 5`, прокидывается дальше как
  deterministic-источник `reporting_standard` (Patch 25 — даже для
  scan-only PDF);
- `reporting_period_year` + `reporting_period_type` — извлекаются
  парсером в `period.py`. Год — любой 4-значный (year-agnostic, без
  верхней/нижней границы); квартал — 1..4; полугодие — 1..2.
  Источник входа — два прохода:

  1. **Anchored full-match** на «Отчётный период» ячейке таблицы:
     - `"YYYY"` (только год) → FY YYYY
     - `"YYYY, 12 месяцев"` → FY YYYY (Patch 477dc1a)
     - `"YYYY, 3/6/9 месяцев"` → Q1 / H1 / 9M
     - `"YYYY, N квартал"` → Q1..Q4 (Patch 477dc1a)
     - `"N квартал YYYY"` / `"I квартал YYYY"` → Q1..Q4
     - `"N полугодие YYYY"` → H1, H2

  2. **Search-mode** на свободном тексте — `<a>`-ссылка, `title`
     attribute и type-label cell конкатенируются, если шаг 1 не дал
     результата (Patch 32):
     - `"за YYYY год"` / `"за YYYY г."` / `"за YYYY года"` → FY YYYY
     - `"Бухгалтерская отчётность за YYYY"` → FY YYYY
     - `"за N квартал YYYY года"` → Q1..Q4 YYYY
     - `"за N полугодие YYYY"` → H1, H2 YYYY

  Search-rules требуют префикс «за …» как анти-false-positive — иначе
  любая случайная 4-значная цифра в свободном тексте угадывалась бы
  как FY.

Если страница `type=N` отдала 200 OK без `table.files-table`, в логах
появляется `discoverer_no_publications_for_type` и Discoverer молча
переходит к следующему URL. Это нормальный кейс: например, LKOH
(`id=17`) не публикует МСФО под этим id, поэтому `type=4` для него
всегда пуст.

**HTTP-бэкенд:**

- **`http_backend: playwright`** (рекомендуется на VPS) — headless
  Chromium запускается один раз за прогон, проходит ServicePipe
  JS-challenge и затем используется для всех HTTP-запросов
  Discoverer + Downloader. JA3 совпадает с настоящим Chrome, cookies
  ставит JS-challenge сам.
- **`http_backend: httpx`** (default из коробки) — `httpx` напрямую,
  без bypass'а. Работает только если у вас есть валидные cookies от
  Chrome, заранее проставленные в `discoverer.cookies` в `app.yaml`.

LLM на этой стадии **не используется**.

---

## 2. Downloader, Unpacker — складываем сырьё на диск

**Файлы:** `src/edx/stages/downloader/service.py`,
`src/edx/stages/unpacker/service.py`.

Downloader для каждой свежей `publication` (status=`discovered`)
скачивает все её файлы под:

```
data/raw/{TICKER}/{PUBLICATION_ID}/{relative_path}
```

Имя файла берётся либо из `Content-Disposition`, либо из последнего
сегмента URL. `mime_type` пишем в `documents.mime_type`. После Patch
477dc1a в случае dedup-skip (файл уже есть на диске и хеш совпадает)
publication всё равно переводится в `downloaded` — иначе при
`--full-reload` все 44 публикации застревали в `discovered`.

Unpacker распаковывает архивы. Patch 85be460 добавил детект по
магическим байтам (`PK\x03\x04` — ZIP, `Rar!\x1a\x07` — RAR), потому
что e-disclosure любит отдавать архивы под именем `FileLoad.ashx`
без расширения. Распакованные файлы становятся новыми `documents`
рядом с архивом.

LLM на этих стадиях не используется. OCR не используется.

---

## 3. Classifier — text vs scan, IFRS vs RSBU vs ISSUER

**Файлы:** `src/edx/stages/classifier/service.py`,
`src/edx/stages/classifier/pdf_inspector.py`,
`src/edx/stages/classifier/heuristics.py`.

Для каждого PDF в публикации делаем две вещи:

### 3.1 Постраничная классификация (Patch 18)

`classify_pages()` открывает PDF через `pymupdf` и для каждой
страницы вызывает `page.get_text("text")`. Если непустых символов
≥ `app.classifier.min_text_chars_per_page` (default **50**) — страница
помечается как `text`, иначе `scan`. Результат — список
`PageClassification(page_index, char_count, kind)`, который
сериализуется в `documents.pages_classification` (JSON-массив).

Документ считается `is_machine_readable=1`, если у него есть **хотя
бы одна** text-страница. Это важно для банковских РПБУ, где первые
30 страниц — текстовый pояснительный нарратив, а потом 80 страниц —
чистые сканы регуляторных форм 0409806/0409807. Раньше такой
документ целиком уходил в OCR (40 минут на CPU); после Patch 18 —
гибридный extract: native текст для первых 30 страниц, OCR только
для оставшихся сканов.

### 3.2 Reporting standard (IFRS / RSBU / ISSUER / OTHER / ANNUAL)

Patch 21 + Patch 25: маппинг из URL берётся первым:

```
type_code=2 → ANNUAL  (annual report, не источник метрик)
type_code=3 → RSBU
type_code=4 → IFRS
type_code=5 → ISSUER
```

Если документ machine-readable, дополнительно запускается текстовый
эвристик `detect_reporting_standard()` по первым 3 страницам:
ищем маркеры МСФО / IFRS / Consolidated / РПБУ / РСБУ. Результат
эвристики сравнивается с `from_url`; при расхождении только для
type=4 (заявленное МСФО без IFRS-маркеров) пишется warning
`classifier_type_code_disagrees_with_text` — это сигнал об
ошибочной публикации на портале. Type=2/5 эвристикой не
проверяются (там по определению mixed-content).

Для **scan-only** PDF (Patch 25) текстовый детектор не запускается —
читать нечего. `reporting_standard` берётся deterministic из
`type_code`. До Patch 25 здесь стоял `OTHER`, и Metric Extractor
отбрасывал всю публикацию.

LLM не используется. OCR не используется.

---

## 4. Text Extractor — JSON по странице

**Файлы:** `src/edx/stages/text_extractor/service.py`,
`src/edx/stages/text_extractor/native.py`,
`src/edx/stages/text_extractor/ocr/tesseract.py`.

Каждый PDF получает JSON-файл под:

```
data/processed/{TICKER}/{PUBLICATION_ID}/{DOCUMENT_ID}.json
```

формата:

```json
{
  "extraction_method": "native" | "ocr_tesseract" | "native+ocr_tesseract",
  "extracted_at": "2026-05-02T03:14:07+00:00",
  "pages": [
    {"page_number": 1, "text": "<очищенный текст>"},
    {"page_number": 2, "text": "..."},
    {"page_number": 3, "text": "...", "tables": [...]}
  ]
}
```

Путь к файлу пишется в `documents.text_extract_path`.

### 4.1 Где включается OCR

OCR-провайдер выбирается в `config/ocr.yaml → engine`. Поддерживается
**только `tesseract`** (cloud-движки `yandex_vision` и `google_vision`
оставлены как заглушки). Запускается через `pytesseract` поверх
`pdf2image`:

1. PDF (или поднабор страниц для гибрида) рендерится в PNG c DPI =
   `config.ocr.tesseract_dpi` (default **400** после Patch 31; на
   русских РСБУ-формах с тонкой grid 300 DPI давал путаницу 8↔3 на
   правых колонках).
2. Для каждой страницы вызывается `pytesseract.image_to_string(image,
   lang="rus+eng", config="--psm 6")` — `--psm 6` (single uniform block
   of text, Patch 31) лучше работает на табличных формах, чем default
   `--psm 3` (auto layout). Языки берутся из `app.text_extractor.ocr_langs`.
3. **Retry** (Patch 31): если первый проход вернул < `tesseract_retry_min_chars`
   (default 80) символов или digit-ratio < `tesseract_retry_min_digit_ratio`
   (default 0.05), повторить с `tesseract_retry_psm` (default 4 — single
   column of variable-sized text, хорошо работает на cover-страницах).
   Берём более длинный output. Лог-событие `tesseract_retry_won` фиксирует,
   что retry сработал. `tesseract_retry_psm: null` отключает retry полностью.
4. Результат — список `PageText(page_number, text)`.

Три ветки на документ:

- **Native (`is_machine_readable=1`, нет scan-страниц):**
  `pymupdf.get_text("text")` per page + `pdfplumber.extract_tables()`
  если включено `text_extractor.extract_tables_enabled: true`.
  Tesseract не запускается — нет смысла OCR-ить страницу, для
  которой PDF уже несёт текстовый слой.
- **Pure OCR (`is_machine_readable=0`):** все страницы через
  Tesseract. Используется на сканах (старые РПБУ, бумажные
  отсканированные годовые отчёты).
- **Hybrid (Patch 18 — `is_machine_readable=1` + `pages_classification`
  содержит `kind: scan`):** native-текст для всех страниц + OCR
  только для тех, что помечены `scan`. Реализация:
  `pymupdf.insert_pdf(src, from_page=idx, to_page=idx)` собирает
  поднабор сканов в временный PDF, OCR прогоняется по нему,
  результат сплайсится обратно в native-список по `page_number`.
  Метод записи — `native+ocr_tesseract`.

### 4.2 Постобработка текста

`normalize_text()` (`text_extractor/normalize.py`):

- удаляет повторяющиеся header/footer (если они встречаются на
  ≥ `header_footer_min_pages` страницах подряд);
- сжимает повторные whitespace, нормализует юникод (NFKC);
- убирает зеро-width пробелы и BOM.

После нормализации общий объём текста публикации обрезается до
`text_extractor.max_chars` (default **400 000** символов) — крайний
бэкстоп от документов на сотни страниц. При обрезке пишется
warning `text_extractor_truncated`.

Tables (`pdfplumber`) сохраняются в `pages[].tables` как массив
массивов строк — в Metric Extractor попадают как сырая JSON-секция
сразу после текста страницы.

LLM на этой стадии не используется.

---

## 5. Metric Extractor — главный потребитель LLM

**Файлы:** `src/edx/stages/metric_extractor/service.py`,
`src/edx/stages/metric_extractor/prompts.py`,
`src/edx/stages/metric_extractor/schema.py`,
`src/edx/stages/metric_extractor/models.py`.

### 5.1 Один вызов LLM на одну публикацию

Для каждой публикации со статусом `extracted` Metric Extractor:

1. **Выбирает source_standard** из `documents` публикации, идя по
   `metrics.yaml → reporting_priority` (default `[IFRS, RSBU, ISSUER]`).
   Если у публикации нет ни одного `IFRS`-документа, перебираем
   следующий стандарт. Документы ANNUAL/OTHER пропускаются.
2. **Подбирает primary document.** Один документ на публикацию
   одного стандарта (для большинства РПБУ/МСФО) или несколько (если
   эмитент выложил отчёт + аудиторское заключение отдельными PDF).
   Текст всех текстовых документов конкатенируется в `user_text`.
3. **Решает, отправлять ли PDF нативно или текстом.** Anthropic
   принимает PDF как `document` content block (≤ 32MB / 100 pages).
   Условие отправки PDF (`send_pdf=True`):
   - провайдер поддерживает PDF (`anthropic.supports_pdf_input=True`,
     OpenRouter — нет);
   - в публикации ровно один документ;
   - этот документ machine-readable (`is_machine_readable=1`);
   - **scan_ratio ≤ `metric_extractor.scan_ratio_threshold`** (Patch 29,
     default 0.10) — `scan_ratio = scan_pages / page_count`. Гибридные
     документы (банковские РСБУ-формы с обложкой-аудитом + сканированными
     формами) уходят на text-path: Anthropic vision не вытягивает цифры
     из тонкой grid-таблицы РСБУ-баланса с подписью главбуха;
   - **standard ∈ `metric_extractor.pdf_input_standards`** (Patch 29,
     default `["IFRS"]`) — эмпирически только IFRS-отчёты CHMF/SBER/VTBR
     надёжно работают через нативный PDF; RSBU и Issuer Report всегда
     уходят на text-path;
   - файл существует на диске.

   Иначе — отправляем текст, собранный из JSON-файлов Text Extractor.
   ISSUER-источник дополнительно обрезается до раздела 1.4 (Patch 21,
   `extract_section_1_4`).
4. **Строит system prompt** через `build_system_prompt(profile,
   source_standard)` — деталях ниже в §5.2.
5. **Строит JSON-схему** через `build_metric_extraction_schema(...)` —
   §5.3.
6. **Делает один вызов** `LLMProvider.complete(LLMRequest(...))`:
   - Anthropic primary → tool_use с `input_schema=json_schema`;
   - OpenRouter fallback (если Anthropic недоступен) → `response_format:
     json_schema` + `json-repair` post-processing для устойчивости.
   - prompt_caching у Anthropic срабатывает на system prompt
     автоматически (1h TTL по умолчанию, Patch 28).

### 5.2 Системный промпт Metric Extractor (build_system_prompt)

Префиксная часть (`_STATIC_PREAMBLE` в `metric_extractor/prompts.py`)
одна и та же для всех (profile, source_standard) пар:

```
Ты — эксперт по извлечению финансовых показателей из публичной российской
отчётности. Твоя задача — вернуть структурированный JSON по строгой
JSON-схеме, описывающий показатели по каждому отчётному периоду из
документа.

Правила:
1. Возвращай только то, что прямо подтверждено текстом документа.
   Не выдумывай числа, не округляй и не пересчитывай суммы.
2. Если показатель не найден — value=null, source_quote=null.
3. Для каждого непустого показателя обязательно укажи source_quote —
   точную короткую цитату из документа (≤ 250 символов).
4. Не нормализуй значения сам: возвращай их как в документе и укажи
   единицу измерения через поле "unit" ("ones" / "thousands" /
   "millions" / "billions"). Конвертацию выполнит пайплайн.
5. Возвращай по одному элементу массива "extractions" на каждый
   отчётный период, найденный в документе. period_type — один из
   Q1, Q2, Q3, Q4, H1, H2, 9M, FY.
6. reporting_standard — IFRS, если документ маркирован как МСФО /
   IFRS / Consolidated, иначе RSBU.
7. Возвращай только JSON — без пояснительного текста и Markdown.
```

К этому склеивается динамический блок «Перечень показателей»: список
canonical-имён метрик профиля с синонимами и hint'ом по единицам:

```
Перечень показателей:
- net_interest_income — целевая валюта RUB (типичные единицы: миллиарды)
  Синонимы: Чистый процентный доход, ЧПД, Net interest income
- net_fee_income — целевая валюта RUB (типичные единицы: миллиарды)
  Синонимы: Чистый комиссионный доход, ЧКД, Net fee and commission income
- net_income — целевая валюта RUB
  Синонимы: Чистая прибыль, Прибыль за период, Net profit
- total_assets — целевая валюта RUB
  Синонимы: Итого активов, Активы, Total assets
- total_equity — целевая валюта RUB
  Синонимы: Итого капитал, Собственный капитал, Total equity
```

Если `source_standard=RSBU` и для какой-то метрики в `metrics.yaml`
есть `aggregation_hint`, добавляется блок:

```
Подсказки по агрегации для РСБУ:
- total_debt: сумма строк баланса 1410 (Долгосрочные заёмные средства)
  + 1510 (Краткосрочные заёмные средства)
```

И в самом конце — приоритет стандартов:

```
Приоритет стандартов отчётности при выборе документа: IFRS > RSBU > ISSUER.
```

`only_in_sources` фильтр **обрезает** список метрик в зависимости от
source_standard. Например, EBITDA (`only_in_sources: [IFRS]`) не
попадает в RSBU-промпт вовсе — LLM физически не имеет слота, в
который её положить, и не пытается «достроить» EBITDA из РПБУ-баланса.

Для пары (profile, source_standard) промпт строится один раз и
кешируется в `MetricExtractorService._prompt_cache` — это даёт
байт-в-байт идентичный prompt для всех публикаций одного типа,
максимизируя hit rate prompt-cache на стороне Anthropic.

### 5.3 JSON-схема ответа

`build_metric_extraction_schema(profile, source_standard)` строит
strict JSON-Schema, который Anthropic enforces через `tool_use`:

```jsonc
{
  "type": "object",
  "properties": {
    "extractions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "reporting_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
          "period_type": {"type": "string", "enum": ["Q1","Q2","Q3","Q4","H1","H2","9M","FY"]},
          "reporting_standard": {"type": "string", "enum": ["IFRS","RSBU","ISSUER"]},
          "currency": {"type": "string", "minLength": 3, "maxLength": 3},
          "unit": {"type": "string", "enum": ["ones","thousands","millions","billions"]},
          "metrics": {
            "type": "object",
            "properties": {
              "net_interest_income": {
                "type": "object",
                "properties": {
                  "value": {"type": ["number","null"]},
                  "source_quote": {"type": ["string","null"]}
                },
                "required": ["value","source_quote"],
                "additionalProperties": false
              },
              "net_fee_income": {/* ... */},
              "net_income": {/* ... */},
              "total_assets": {/* ... */},
              "total_equity": {/* ... */}
            },
            "required": ["net_interest_income","net_fee_income","net_income","total_assets","total_equity"],
            "additionalProperties": false
          }
        },
        "required": ["reporting_date","period_type","reporting_standard","currency","unit","metrics"],
        "additionalProperties": false
      }
    }
  },
  "required": ["extractions"],
  "additionalProperties": false
}
```

Схема **запрещает additionalProperties** на всех уровнях — это
гарантирует, что LLM не «обогатит» ответ неподдерживаемыми полями.

### 5.4 Что попадает в user_text / PDF

Зависит от ветки `send_pdf`:

**Случай PDF (Anthropic, machine-readable, не ISSUER):**

```
=== content blocks (Anthropic /v1/messages) ===
[0] type: document, source: base64(application/pdf, raw_bytes)
[1] type: text, text:
    Эмитент: SBER (профиль bank). Стандарт: IFRS.
    Извлеки финансовые показатели из приложенного документа.
```

PDF идёт целиком (Anthropic сам делает OCR при необходимости поверх
своих внутренних моделей — но мы не полагаемся на это для scan-only
документов, потому что для них у нас ветка text-extract).

**Случай text (OpenRouter, или scan-документ, или ISSUER, или
multi-document публикация):**

```
=== messages.user.content (Anthropic format) ===
[0] type: text, text:
    Эмитент: SBER (профиль bank). Стандарт отчётности: IFRS.

    === Документ: SBERBANK_IFRS_Q1_2025.pdf ===
    --- page 1 ---
    <текст страницы 1, нормализованный normalize_text>
    --- page 2 ---
    <текст страницы 2>
    ...
    --- page N ---
    <текст последней страницы>

    === Документ: AUDITOR_OPINION.pdf ===
    --- page 1 ---
    ...
```

Для ISSUER (type=5) перед склейкой текст обрезается до раздела 1.4
«Основные финансовые показатели» через `extract_section_1_4()`
(regex по якорным заголовкам + TOC-эвристика). Если найти раздел
не удалось, шлём полный текст и пишем warning
`metric_extract_issuer_trim`. К системному prompt'у добавляется
дополнительная подсказка:

```
Перед тобой раздел 1.4 «Основные финансовые показатели»
ежеквартального отчёта эмитента. Извлекай значения только из
сводных KPI-таблиц этого раздела; не пытайся достроить то, чего нет.
```

Для **RSBU** (Patch 30) перед склейкой текст обрезается до первого
из якорей балансовой формы через `extract_balance_sheet_onwards()`:

- `БУХГАЛТЕРСКИЙ БАЛАНС` / `Бухгалтерский баланс` (заголовок формы
  на отдельной строке, case-insensitive);
- `Форма по ОКУД 0710001` (код Минфина для РСБУ-баланса, ловит и
  `710001` без leading zero);
- `ОТЧЁТ О ФИНАНСОВЫХ РЕЗУЛЬТАТАХ` (fallback — если баланс идёт
  после ОФР, или его не удалось распознать).

Срезается всё **до** якоря — обычно это многостраничное аудиторское
заключение (Кэпт / Б1 / ДРТ / Делойт preamble: 5–30k символов
методологии и ключевых вопросов аудита), не несущее KPI и
рискующее запутать LLM (фразы вроде «прибыль 100 млн» в контексте
обоснования аудиторской процедуры). К trimmed-тексту приклеивается
короткий header `Перед тобой формы РСБУ-отчётности (баланс, ОФР,
отчёт об изменениях капитала)…`. Если ни один якорь не найден —
fail-soft: шлём полный текст и пишем warning
`metric_extract_balance_anchor_missing`.

### 5.5 Что приходит обратно

LLM возвращает один JSON-объект:

```json
{
  "extractions": [
    {
      "reporting_date": "2025-03-31",
      "period_type": "Q1",
      "reporting_standard": "IFRS",
      "currency": "RUB",
      "unit": "billions",
      "metrics": {
        "net_interest_income": {
          "value": 838.4,
          "source_quote": "Чистый процентный доход за 1 квартал 2025 года составил 838,4 млрд рублей"
        },
        "net_fee_income": {"value": 217.7, "source_quote": "..."},
        "net_income": {"value": 411.4, "source_quote": "..."},
        "total_assets": {"value": 56324.8, "source_quote": "..."},
        "total_equity": {"value": 7102.3, "source_quote": "..."}
      }
    },
    {
      "reporting_date": "2024-03-31",
      "period_type": "Q1",
      "reporting_standard": "IFRS",
      "currency": "RUB",
      "unit": "billions",
      "metrics": { "...": "comparative-prior period block" }
    }
  ]
}
```

### 5.6 Что пайплайн делает с этим JSON

1. **Pydantic-валидация** через `MetricExtractionResult.model_validate(data)`
   — гарантирует enum-значения и числовые типы.
2. **Drop comparative periods (Patch 27).** Если у публикации
   проставлены `reporting_period_year` / `reporting_period_type`
   (Patch 17), все элементы `extractions[]` с не совпадающим
   `period_type` отбрасываются. Это убирает второй блок из примера
   выше — comparative prior Q1 2024 принадлежит другой
   публикации (IFRS Q1 2024) и должен заполняться оттуда.
3. **Dedup внутри публикации (Patch 26).** Иногда LLM эмитирует две
   записи с одинаковым `(reporting_date, period_type, reporting_standard)`
   — например, ту же квартальную таблицу из двух разделов документа.
   Схлопываем до одной строки на ключ
   `(date, period_type, std, metric_name)`, предпочитая non-null
   value; при двух non-null — последний выигрывает (LLM обычно
   ставит самый свежий период последним).
4. **Нормализация unit.** `value=838.4` + `unit=billions` →
   `838_400_000_000.0` (TARGET_UNIT=`ones`). Записывается в БД
   как нормализованный rubль.
5. **`metrics_repo.replace_for_publication()`.** Делает атомарное:
   ```sql
   DELETE FROM metrics WHERE source_document_id IN (
     SELECT document_id FROM documents WHERE publication_id = ?
   );
   INSERT OR REPLACE INTO metrics (...) VALUES (...);
   ```
   `INSERT OR REPLACE` (Patch 27) защищает от cross-publication
   коллизий по UNIQUE-индексу `(ticker, reporting_date, period_type,
   reporting_standard, metric_name)`: «победитель — последний
   write», `source_document_id` тоже обновляется.
6. **Coverage.** `extracted_count = sum(value is not None)`,
   `requested_count = len(applicable_metrics) * len(periods)`,
   `coverage_ratio = extracted/requested`. Если ratio <
   `validator.completeness_threshold` (default **0.5**) — публикация
   помечается `is_incomplete=1`.

### 5.7 Метрики наблюдаемости (Patch 28)

Каждое событие `metric_extract_completed` в `pipeline.log` несёт:

```json
{
  "event": "metric_extract_completed",
  "publication_id": "...",
  "profile": "bank",
  "standard": "IFRS",
  "periods": 1,
  "rows_written": 5,
  "extracted": 5,
  "requested": 5,
  "coverage_ratio": 1.0,
  "is_incomplete": false,
  "input_tokens": 18432,
  "output_tokens": 412,
  "cache_read_input_tokens": 1547,
  "cache_creation_input_tokens": 0,
  "cache_hit_ratio": 0.084
}
```

`cache_hit_ratio` — `cache_read / input_tokens`. На второй и
последующих публикациях прогона он должен быть в районе 0.05–0.15
(системный промпт ~1.5к токенов из ~20–50к user_text). Ratio = 0
устойчиво на всех публикациях — сигнал, что
`enable_prompt_caching: false` или системный промпт нестабилен (это
можно увидеть, если зачем-то крутить `metrics.yaml` между вызовами).

---

## 6. Event Extractor — сообщения о существенных фактах

**Файлы:** `src/edx/stages/event_extractor/service.py`,
`src/edx/stages/event_extractor/prompts.py`,
`src/edx/stages/event_extractor/schema.py`.

Источник — публикации `publication_type='event'` (отдельный feed
от Discoverer). Для каждой такой публикации делается **один вызов
LLM на одно сообщение**.

### 6.1 Системный промпт

```
Ты — эксперт по разбору сообщений о существенных фактах российских
эмитентов (e-disclosure.ru). Каждое сообщение — это одно событие.
Твоя задача — вернуть строгий JSON по предоставленной схеме.

Правила:
1. Возвращай только то, что прямо подтверждено текстом. Не выдумывай
   даты, имена и числа.
2. event_type — выбери код из списка ниже. Если ни один не подходит,
   используй "other".
3. event_date — фактическая дата события (например, дата сделки или
   решения), формат YYYY-MM-DD. Если дата не указана явно, верни null.
4. publication_date — дата публикации сообщения, YYYY-MM-DD; если в
   тексте не указана, верни null.
5. summary — 1–3 предложения по-русски, не более 600 символов.
   Не пересказывай шапку и реквизиты эмитента.
6. key_params — объект с ключевыми числовыми параметрами (например,
   сумма сделки, размер дивиденда, доля участия). Значения — числа,
   строки или null. Если ключевых параметров нет, верни {}.
7. Возвращай только JSON, без Markdown и пояснительного текста.

Справочник типов событий:
- dividend_decision: Решение о выплате дивидендов (синонимы: dividends, дивиденды)
- material_contract: Существенный договор (...)
- ...
- other: Прочие события
```

Список типов и синонимы берутся из `config/event_types.yaml`.

### 6.2 JSON-схема

```json
{
  "type": "object",
  "properties": {
    "event_type": {"type": "string", "enum": ["<коды из event_types.yaml>"]},
    "event_date":       {"type": ["string", "null"], "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
    "publication_date": {"type": ["string", "null"], "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
    "summary":          {"type": "string", "maxLength": 600},
    "key_params": {
      "type": "object",
      "additionalProperties": {"type": ["string","number","boolean","null"]}
    }
  },
  "required": ["event_type","event_date","publication_date","summary","key_params"],
  "additionalProperties": false
}
```

### 6.3 Контекст вызова

Текст события собирается так (`_build_user_text` в
`event_extractor/service.py`):

1. **Приоритет 1** — text-extract из Text Extractor (если документ
   проходил классификацию и был обработан): склеиваются все
   страницы с `--- page N ---` разделителями, в шапке
   `Эмитент: TICKER\nИсточник: <source_url>`.
2. **Приоритет 2** — оригинальный HTML/PDF из `data/raw/`:
   `html_to_text(html)` срезает скрипты, теги, оставляет plain text.

Один такой блок и есть весь user_text. PDF нативно не отправляем —
сообщения короткие, текста достаточно.

### 6.4 Ответ и запись

LLM возвращает один объект:

```json
{
  "event_type": "dividend_decision",
  "event_date": "2025-04-25",
  "publication_date": "2025-04-25",
  "summary": "Совет директоров рекомендовал годовое собрание утвердить дивиденды за 2024 год в размере 34,84 рубля на одну обыкновенную акцию.",
  "key_params": {
    "amount_per_share_rub": 34.84,
    "fiscal_year": 2024,
    "share_class": "ordinary"
  }
}
```

`key_params` сериализуется как `key_params_json` (для упрощения
запросов к БД и Excel — там колонка одна, плоская строка). Запись
делается через `events_repo.upsert(...)` ключом
`(ticker, source_url)`.

---

## 7. Validator — что отсекаем после LLM

**Файл:** `src/edx/stages/validator/service.py`.

Sanity-checks поверх записанных метрик:

- **Знаки.** `total_assets`, `revenue`, `total_equity` должны быть
  ≥ 0; `net_income` может быть отрицательным.
- **Магнитуда.** Если `value` отличается от медианы своего тикера +
  metric_name за последние 4 квартала более чем в 10×, ставится
  `qa_warning='magnitude_jump'`.
- **Currency.** Допустимые `currency` — `RUB`, `USD`, `EUR`. Прочие
  → `qa_warning='unexpected_currency'`.
- **Completeness.** Уже посчитан в Metric Extractor; флаг
  `publications.is_incomplete` использует его.

Validator пишет `qa_warning` в строки `metrics`, и отдельные жалобы
— в `qa_issues` (попадают на отдельный лист Excel).

LLM не используется. OCR не используется.

---

## 8. Writer — формирование Excel

**Файлы:** `src/edx/stages/writer/excel.py`,
`src/edx/stages/writer/service.py`.

Excel-витрина (`output/e-disclosure.xlsx`) состоит из 4 листов:

### 8.1 Лист `metrics`

Источник — `MetricsRepo.list_all_for_export()`:

```sql
SELECT m.metric_id, m.ticker, m.reporting_date, m.period_type,
       m.reporting_standard, m.metric_name, m.value, m.currency,
       m.unit, m.qa_warning, p.source_url AS source_publication_url
FROM metrics m
JOIN documents d ON d.document_id = m.source_document_id
JOIN publications p ON p.publication_id = d.publication_id
ORDER BY m.ticker, m.reporting_date, m.period_type, m.metric_name;
```

Колонки в Excel совпадают 1-в-1:

| ticker | reporting_date | period_type | reporting_standard | metric_name | value | currency | unit | qa_warning | source_publication_url |
|---|---|---|---|---|---|---|---|---|---|
| SBER | 2025-03-31 | Q1 | IFRS | net_interest_income | 838 400 000 000 | RUB | ones | | https://www.e-disclosure.ru/portal/event.aspx?... |

`value` — нормализованное число в `ones` (см. §5.6 шаг 4).
Excel-форматирование: целые → `#,##0`, дробные → `#,##0.##`,
ширина колонок auto-fit, freeze panes на A2.

### 8.2 Лист `events`

| ticker | event_date | publication_date | event_type | summary | key_params_json | source_url |
|---|---|---|---|---|---|---|
| SBER | 2025-04-25 | 2025-04-25 | dividend_decision | Совет директоров рекомендовал… | {"amount_per_share_rub": 34.84, …} | https://… |

### 8.3 Лист `tickers` (Patch 19)

Список тикеров из `config/tickers.yaml`, чтобы аналитик видел, по
каким эмитентам какой профиль метрик применялся:

| ticker | name | profile | e_disclosure_id |
|---|---|---|---|
| SBER | ПАО Сбербанк | bank | 3043 |
| LKOH | ПАО "ЛУКОЙЛ" | non_bank | 17 |

### 8.4 Лист `meta`

Key-value, по одной строке на параметр:

| key | value |
|---|---|
| last_updated_at | 2026-05-02T03:14:07+00:00 |
| pipeline_version | 1.0.0 |
| tickers_count | 51 |
| metrics_rows | 1247 |
| events_rows | 89 |
| incomplete_publications | 4 |
| failed_publications | 1 |

### 8.5 Лист `qa_issues`

| ticker | publication_id | code | message | created_at |
|---|---|---|---|---|
| SBER | 4-1893774 | magnitude_jump | net_interest_income jumped 12.4× vs 4Q median | 2026-05-02T03:14:07+00:00 |

### 8.6 Атомарная запись

Workbook сохраняется в `<target>.tmp` и затем `os.replace`'ом
встаёт на место. При сбое посередине — старый файл остаётся
неизменным (это критично для синхронизации с Google Drive: ссылка
для пользователя iPhone не должна сломаться, даже если очередной
прогон упал на Tesseract'е).

---

## 9. Replicator — загрузка на Google Drive

**Файл:** `src/edx/stages/writer/replicator.py`.

Через Google Drive API (`google-api-python-client` + `httplib2` с
прокси-поддержкой Patch 24) загружается **тот же `file_id`**, что и
в прошлый раз — Drive обновляет содержимое, а ссылка не меняется.
`file_id` хранится в `runs.excel_drive_file_id`.

Опционально (`google_drive.archive: true`) дополнительно создаётся
датированный снапшот в подпапке `archive/{YYYY-MM-DD}/e-disclosure.xlsx`.

Проксирование (Patch 24): `httplib2.proxy_info_from_environment`
читает `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` из env. e-disclosure
обходит прокси через `NO_PROXY=e-disclosure.ru,...`. Логируется
событие `drive_proxy_configured` с заполненными `proxy_url` /
`no_proxy` — маркер успешного подхвата.

LLM не используется. OCR не используется.

---

## 10. Что хранится между прогонами

| Что | Где | Когда обновляется |
|---|---|---|
| Состояние публикаций | `data/state.sqlite` | каждый этап |
| Скачанные сырьё | `data/raw/{TICKER}/{PUB_ID}/` | Downloader/Unpacker |
| Text-extracts | `data/processed/{TICKER}/{PUB_ID}/{DOC_ID}.json` | Text Extractor |
| Кеш ответов LLM | `data/processed/_llm_cache/{sha256}.json` | при `cache_enabled: true` (default) |
| Excel-витрина | `output/e-disclosure.xlsx` | Writer |
| Логи | `logs/pipeline.log` (JSON, ротация 10MB×5) | каждый этап |

`_llm_cache` ключуется SHA-256 от `system + user_text + pdf_bytes
+ json_schema`. Идемпотентно: повторный прогон по той же публикации
не идёт в LLM, читает с диска. Чистится `edx cache prune
--older-than 30d`.

Кеш на стороне Anthropic (prompt caching, Patch 28) — независим:
он работает в рамках 1h TTL для одного API-key. Пайплайн его не
управляет напрямую, только включает (`enable_prompt_caching: true`)
и наблюдает (`cache_hit_ratio` в логах).

---

## 11. Чек-лист отладки «почему числа неправильные»

| Симптом | Что посмотреть |
|---|---|
| Колонка `value` пустая для всех публикаций тикера | `documents.reporting_standard` — если `OTHER`, нужен Patch 25-style фикс или поправить `e_disclosure_id` |
| Несколько строк за один период (например, два `net_income` в Q1 2025) | Не должно случаться после Patch 27 + Patch 26; смотреть `metric_extract_dropped_comparative_periods` и `metric_extract_duplicate_period` в логах |
| Числа отличаются от документа в 1000 раз | Проверить `unit` в Excel; если в документе «млрд», а пайплайн записал `ones=838400` (вместо `838400000000`) — LLM вернул `unit=ones` вместо `unit=billions`. Source_quote в LLM-ответе обычно показывает, как LLM прочитал |
| `coverage_ratio < 0.5` на одной публикации | `metric_extract_completed` лог: какой `standard` выбран, сколько `requested`. Если все 0, обычно проблема в `text_extract_path` (Text Extractor не дошёл до этой публикации) |
| Сплошные `discoverer_no_publications_for_type` | ServicePipe — переключиться на `playwright` (USER_GUIDE → ServicePipe / headless Chromium) |
| 429 от Anthropic при `--full-reload` | ITPM tier; user_text доминирует над system prompt, prompt caching не спасает целиком — поднимать tier или ставить меньшую `concurrency` в `llm.yaml` |
| `cache_hit_ratio: 0.0` устойчиво | Проверить `enable_prompt_caching: true`; не менять `metrics.yaml` / `tickers.yaml` между прогонами; `cache_ttl: 5m` слишком короток для длинных прогонов — переключить в `1h` |
| `coverage=0` при `input_tokens ≈ 3100, output_tokens ≈ 133` для RSBU-документа | Маршрутизация ушла в native-PDF путь Anthropic, и Anthropic vision не справился. Проверить `metric_extract_start.send_pdf` в логе — для RSBU должно быть `false`. Если `true` — `metric_extractor.pdf_input_standards` неверно сконфигурирован (Patch 29) |
| Слишком много шума в LLM-ответе на RSBU-документе (LLM «придумывает» цифры из аудиторского preamble) | Проверить `metric_extract_balance_anchor_trimmed` в логе — должно появиться для каждого RSBU. Если стоит `metric_extract_balance_anchor_missing` — сначала разобраться, почему якорь не найден (битый OCR-текст, нестандартный formatting), потом усиливать regex (Patch 30) |

Полный JSON-лог одного прогона восстанавливает всю картину
end-to-end:

```bash
jq -r 'select(.publication_id == "4-1893774") | "\(.ts) \(.event) \(.message // "")"' logs/pipeline.log
```

Это даст последовательность всех событий по одной публикации от
`discoverer_publication_found` до `replicator_uploaded`.
