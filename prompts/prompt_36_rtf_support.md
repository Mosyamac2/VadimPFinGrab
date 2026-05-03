# Промпт 36. RTF-документы как text-source в Text Extractor / Classifier

## Цель

Добавить поддержку формата `.rtf` (Rich Text Format) в стадиях
Classifier и Text Extractor. Run_id=14 показал, что часть эмитентов
выкладывает Issuer Report (type=5) не в PDF, а в RTF:

- `PHOR-5-1897741` — `_unpacked/отчет эмитента 6 мв 2025.rtf`
- `SLGD-5-1899511` — `_unpacked/Отчет эмитента за 6 мес 2025_Селигдар_на сайт.rtf`

Текущий пайплайн фильтрует `_is_pdf(doc)` на обеих стадиях, и RTF
молча отбрасывается. В state.sqlite такие документы остаются с
`reporting_standard=NULL, is_machine_readable=NULL`, и Metric
Extractor пишет `metric_extract_no_eligible_docs` → publication
помечается `skipped`.

После Patch 36: RTF-документы парсятся через `striprtf` (pure-Python,
без системных зависимостей), классифицируются по `type_code`
(deterministic mapping как в Patch 25), и идут на text-path в Metric
Extractor наравне с PDF.

## Контекст

- `striprtf>=0.0.27` — pure-Python, MIT-licensed, обрабатывает все
  кейсы российских эмитентов (Word-RTF, OpenOffice-RTF, чистый RTF
  по спеке Microsoft 1.5+). Альтернатива `pypandoc` требует
  системного `pandoc` — не подходит для VPS.
- RTF не имеет понятия страниц до OCR; считаем `page_count=1` и
  кладём весь текст в одну `PageText(page_number=1, text=...)`.
- `is_machine_readable` для RTF всегда `1` — текст уже в plain
  form, OCR не нужен.
- `pages_classification` для RTF — `null` (нет смысла).
- **Зависимости:** независим от Patch 35/37. Опирается на Patch 25
  (deterministic type_code mapping) — он уже в master.

## Задачи

### 1. `pyproject.toml`

В `[project.dependencies]` добавить:

```toml
striprtf>=0.0.27
```

Не делать optional-extra — RTF появляется в production'е, не
testing-only.

### 2. `src/edx/stages/text_extractor/native.py`

Добавить функцию (рядом с существующей `extract_text`):

```python
def extract_text_from_rtf(rtf_path: Path) -> list[PageText]:
    """Read .rtf file, strip control words, return single-page text.

    RTF doesn't carry page boundaries reliably (Word-RTF emits
    \\page only when the user inserts a manual break), so we treat
    the whole document as one synthetic "page 1". Downstream
    Metric Extractor concatenates pages anyway — single page just
    means one ``--- page 1 ---`` marker in the user_text.
    """
    from striprtf.striprtf import rtf_to_text

    raw = rtf_path.read_text(encoding="utf-8", errors="replace")
    plain = rtf_to_text(raw, errors="ignore")
    return [PageText(page_number=1, text=plain or "")]
```

### 3. `src/edx/stages/text_extractor/service.py`

В `_extract_one`, расширить условие фильтрации документов. Сейчас
там `if not _is_pdf(doc): continue`. Заменить на полиморфный
выбор:

```python
def _document_extractor(self, doc: DocumentRow) -> str | None:
    """Return 'pdf', 'rtf' or None — None means skip."""
    if _is_pdf(doc):
        return "pdf"
    if _is_rtf(doc):
        return "rtf"
    return None
```

`_is_rtf`:

```python
RTF_MIME_PREFIXES = ("application/rtf", "text/rtf")
RTF_SUFFIXES = (".rtf",)


def _is_rtf(doc: DocumentRow) -> bool:
    if doc.mime_type and any(
        doc.mime_type.startswith(prefix) for prefix in RTF_MIME_PREFIXES
    ):
        return True
    return doc.relative_path.lower().endswith(RTF_SUFFIXES)
```

В loop по документам — для RTF use a dedicated branch:

```python
kind = self._document_extractor(doc)
if kind is None:
    continue
if kind == "rtf":
    pages = extract_text_from_rtf(full_path)
    method = "rtf_native"
    native += 1
else:  # kind == "pdf"
    # existing PDF logic (native / OCR / hybrid)
    ...
```

`is_machine_readable` для RTF — по умолчанию None (Classifier его
выставит позже). На этой стадии не дёргаем.

### 4. `src/edx/stages/classifier/service.py`

В `_classify_one`, расширить условие — сейчас
`if not _is_pdf(doc): continue`. Делаем полиморфный выбор:

```python
if _is_pdf(doc):
    # existing PDF classification (per-page, OCR detection, etc.)
    ...
elif _is_rtf(doc):
    self.documents_repo.update_classification(
        doc.document_id,
        reporting_standard=reporting_standard_for_type_code(
            pub.report_type_code
        ),
        report_form="other",  # RTF не несёт form code
        is_machine_readable=True,
        page_count=1,
        pages_classification=None,
        text_pages_count=1,
        scan_pages_count=0,
    )
    standards_counter += 1  # bump aggregate counter
else:
    continue
```

Импортировать `_is_rtf` из text_extractor.service либо вынести в
общий module `src/edx/stages/_doc_kind.py` (минимальный
shared-helper). Я бы вынес — это чище.

Альтернатива: добавить `_is_rtf` локально и в classifier и в
text_extractor (DRY-нарушение, но 4 строки кода — приемлемо).

### 5. Тесты

`tests/stages/text_extractor/test_rtf.py` (новый файл):

- `test_extract_text_from_rtf_returns_single_page`: фикстура
  `tests/fixtures/rtf/sample.rtf` (минимальный RTF с одной
  строкой кириллицы) → `[PageText(page_number=1, text="<text>")]`.
- `test_extract_text_from_rtf_preserves_cyrillic`: RTF с
  «Чистая прибыль 1 234 567» → возвращённый text содержит ту же
  фразу.
- `test_extract_text_from_rtf_strips_control_words`: RTF с
  `\par\b\i` markup → text без них.
- `test_extract_text_from_rtf_handles_empty_file`: пустой `.rtf`
  → `[PageText(page_number=1, text="")]` (graceful, no exception).

`tests/stages/text_extractor/test_service.py`:

- `test_text_extractor_processes_rtf_document`: фикстура с RTF на
  диске + DocumentInput с mime=`application/rtf` → state получает
  `extraction_method="rtf_native"`, JSON содержит page_number=1.

`tests/stages/classifier/test_service.py`:

- `test_classifier_handles_rtf_with_type_code_mapping`: фикстура
  RTF, type_code=5 → `reporting_standard='ISSUER',
  is_machine_readable=1, page_count=1`.

Минимальный `tests/fixtures/rtf/sample.rtf`:

```
{\rtf1\ansi\ansicpg1251
Отчёт эмитента ПАО «Тестовая Компания» за 6 месяцев 2025 года.
Чистая прибыль 1 234 567 тыс. руб.
}
```

Можно сгенерировать программно в conftest.py (более устойчиво).

### 6. `PIPELINE_LOGIC.md`

§4 (Text Extractor) — в начале добавить, что стадия теперь
поддерживает PDF и RTF. RTF идёт через `extract_text_from_rtf`
(нет OCR-веток). §3 (Classifier) — упомянуть, что RTF
auto-mapping `is_machine_readable=1, page_count=1`,
reporting_standard от type_code.

§11 (debug) — строка «`metric_extract_no_eligible_docs` для type=5
PHOR/SLGD → проверить, есть ли RTF-документ; если да — после
Patch 36 он должен пройти через text-path».

### 7. `USER_GUIDE.md`

В разделе «Системные требования» (§2) ничего не добавлять — RTF
требует только Python lib, не системный пакет. В разделе «Если
что-то не работает» дополнить: «`metric_extract_no_eligible_docs`
для тикера, у которого Issuer Report публикуется как .rtf — после
Patch 36 это должно работать; если нет, проверить что
`pip install striprtf>=0.0.27` прошёл».

## Тесты, которые должны проходить

- 4 + 1 + 1 = 6 новых тестов зелёные.
- Существующие тесты Text Extractor / Classifier не сломаны.
- `make lint typecheck test` зелёный.
- Зависимость `striprtf` устанавливается через `pip install -e .`
  без системных требований.

## Definition of Done

- На фейковом RTF-документе с известным содержимым (фикстура)
  Text Extractor возвращает корректный plain text.
- В state.sqlite после следующего `edx run --full-reload`
  публикации `PHOR-5-1897741` и `SLGD-5-1899511` имеют
  `status='written'` (а не `'skipped'` с
  `last_error="no document matches profile reporting_priority"`).
- В Excel mart этих публикаций появляются строки с метриками из
  Issuer Report.
- `PIPELINE_LOGIC.md` + `USER_GUIDE.md` обновлены.
