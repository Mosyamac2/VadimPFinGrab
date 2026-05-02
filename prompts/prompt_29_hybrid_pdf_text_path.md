# Промпт 29. Hybrid-PDF и RSBU всегда идут через text-extract path

## Цель

Запретить отправку PDF нативно в Anthropic, когда документ либо
гибридный (значимая доля сканированных страниц), либо составлен по
русским РСБУ-формам. Live-прогон `run_id=13` показал, что
`MetricExtractorService` выбирает `sends_pdf: true` для всех
машинно-читаемых одиночных PDF без оглядки на то, *что* в этих PDF, и
для шести публикаций подряд получает `coverage_ratio=0.0` при
`input_tokens ≈ 3100, output_tokens ≈ 133` — Anthropic видит сканы
балансовых форм, переходит на свой vision-путь и не вытягивает цифры
из тонкой grid-таблицы. Жертвы (см. state-latest.sqlite, run_id=13):
`CHMF-3-1913112` (FS_31122025.pdf, 6 text + 30 scan), `VTBR-3-1924077`
(43 text-страницы, ноль сканов — но всё равно coverage 0%),
`SBER-3-1905283`, `SBER-3-1914523`, `VTBR-3-1912809`,
`VTBR-4-1893774`. Hybrid-OCR text, который мы аккуратно собрали в
Text Extractor (для FS_31122025.pdf — 86 615 символов), на этом пути
выкидывается в мусор.

Patch 29 закрывает основную дыру: в условие `send_pdf` добавляется
два требования — низкий `scan_ratio` и «PDF-friendly» стандарт
отчётности (`IFRS`). Всё остальное идёт через text-path с собранным
нативным/OCR-текстом.

## Контекст

- Корневой анализ (диагностика, доказательства, scope) — в
  `PIPELINE_LOGIC.md` §5 + history-блоке за 2026-05-02 разбора
  state-latest.sqlite + pipeline-latest.log. Краткое резюме: только
  IFRS-отчёты (CHMF/SBER/VTBR консолидированные) надёжно работают
  через нативный PDF-input Anthropic; всё RSBU и любые гибриды дают
  coverage=0%.
- **Зависимости:** не зависит от других патчей серии 29–34. Является
  P0-блокером для всей серии — без него Patch 30/31 нечего
  улучшать (LLM просто не получает текст).
- Не трогает Discoverer / Downloader / Classifier / Text Extractor —
  данные на диске и в `documents.text_extract_path` уже корректны;
  чинится только маршрутизация в Metric Extractor.

## Задачи

### 1. `src/edx/stages/metric_extractor/service.py`

Точка изменения — функция `_build_request` (текущие строки ~318–369).
Условие `send_pdf` сейчас:

```python
send_pdf = (
    self.llm_provider.supports_pdf_input
    and len(chosen) == 1
    and primary_doc.is_machine_readable == 1
    and full_path.is_file()
    and standard != "ISSUER"
)
```

Изменить на:

```python
scan_ratio = (
    primary_doc.scan_pages_count / primary_doc.page_count
    if primary_doc.page_count
    else 1.0
)
is_low_scan = scan_ratio <= self.scan_ratio_threshold
is_pdf_friendly = standard in self.pdf_input_standards
send_pdf = (
    self.llm_provider.supports_pdf_input
    and len(chosen) == 1
    and primary_doc.is_machine_readable == 1
    and is_low_scan
    and is_pdf_friendly
    and full_path.is_file()
)
```

Условие `standard != "ISSUER"` уходит — его роль теперь играет
`is_pdf_friendly`, у которого ISSUER по дефолту не входит в
`pdf_input_standards`.

В `__init__` добавить два поля:

```python
scan_ratio_threshold: float = 0.10,
pdf_input_standards: tuple[str, ...] = ("IFRS",),
```

Сохранить как self-атрибуты. Обновить блок логирования
`metric_extract_start` — добавить ключи `scan_ratio` и
`pdf_input_standards` в payload, чтобы оператор видел причину
маршрутизации в `pipeline.log`.

### 2. `src/edx/config/app_config.py`

В блок-классе `MetricExtractorConfig` (если есть; иначе — добавить
секцию или расширить ту, в которой уже живёт `completeness_threshold`
и `issuer_trim_max_chars`) добавить:

```python
scan_ratio_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
pdf_input_standards: tuple[Literal["IFRS", "RSBU", "ISSUER"], ...] = ("IFRS",)
```

`tuple` нужен потому, что Pydantic считает `list` mutable default.

### 3. `src/edx/stages/metric_extractor/factory.py`

Прокинуть оба новых параметра из `settings.app.metric_extractor` в
`MetricExtractorService(...)`. Если фабричный модуль читает не из
`metric_extractor`, а из `app.metric_extractor` или другого места —
сохранить consистентный путь.

### 4. `config/app.yaml`

Под существующую секцию `metric_extractor:` (или создать, если
её ещё нет) добавить:

```yaml
metric_extractor:
  completeness_threshold: 0.5
  issuer_trim_max_chars: 30000
  # Patch 29: оборона от Anthropic native-PDF на сканированных формах.
  # Если у документа доля scan-страниц > порога — игнорируем native-PDF
  # путь и шлём наш собственный hybrid-OCR-текст. Дефолт 0.10 пускает
  # IFRS-отчёты с 1-2 cover-сканами, и режет всё с реальной долей сканов.
  scan_ratio_threshold: 0.10
  # Patch 29: для каких стандартов отчётности разрешено посылать PDF
  # нативно. RSBU-формы и Issuer Report Anthropic vision не вытягивает —
  # их шлём как text. IFRS-отчёты CHMF/SBER/VTBR работают надёжно.
  pdf_input_standards: ["IFRS"]
```

Существующие knobs (`completeness_threshold`, `issuer_trim_max_chars`)
не трогать — только дополнить.

### 5. Тесты

Создать или расширить
`tests/stages/metric_extractor/test_send_pdf_routing.py`:

- `test_pure_text_ifrs_pdf_keeps_pdf_path`: фикстурный
  `DocumentRow(is_machine_readable=1, page_count=14,
  text_pages_count=13, scan_pages_count=1)`, `standard="IFRS"`. Ожидаем
  `req.pdf_bytes is not None` и `user_text` короткий
  («Извлеки финансовые показатели из приложенного документа.»).
- `test_hybrid_pdf_falls_back_to_text_path`: `page_count=36,
  text_pages_count=6, scan_pages_count=30`, `standard="RSBU"`.
  Ожидаем `req.pdf_bytes is None`, и `user_text` содержит маркер
  страницы (`--- page 1 ---`).
- `test_rsbu_pure_text_pdf_still_uses_text_path`: `page_count=43,
  text_pages_count=43, scan_pages_count=0`, `standard="RSBU"`.
  Несмотря на нулевой scan-ratio, `pdf_input_standards=("IFRS",)`
  не включает RSBU → `pdf_bytes is None`. Это и есть «банковский
  РСБУ Q1 2026 case».
- `test_threshold_boundary_at_10_percent`: scan_ratio=0.10 → PDF
  путь (граница включена). scan_ratio=0.11 → text-путь.
- `test_issuer_always_uses_text_path`: standard="ISSUER" с
  scan_ratio=0 → `pdf_bytes is None` (`pdf_input_standards` не
  включает ISSUER).
- `test_zero_page_count_treats_as_full_scan`: `page_count=0` (битый
  PDF) → scan_ratio=1.0 → text-путь.
- `test_metric_extract_start_log_includes_routing_keys`: проверить
  через `caplog`, что событие `metric_extract_start` несёт
  `scan_ratio` и `pdf_input_standards`.

Для всех тестов использовать фейковый `LLMProvider` (как в существующих
`tests/stages/metric_extractor/test_service.py`), не дёргать настоящий
Anthropic.

### 6. `PIPELINE_LOGIC.md`

В §5.1 (Metric Extractor — выбор PDF vs text) расширить пункт «Решает,
отправлять ли PDF нативно или текстом» — добавить два новых условия
(`scan_ratio` и `pdf_input_standards`) с короткой мотивацией.

В §11 («Чек-лист отладки») добавить строку: «coverage=0 при
input_tokens ≈ 3100 → проверьте, что `metric_extract_start.send_pdf
== false` для RSBU-документа; если true — `pdf_input_standards`
неверно сконфигурирован».

## Тесты, которые должны проходить

- Все 7 новых тестов выше зелёные.
- Существующие тесты `tests/stages/metric_extractor/*` не сломаны.
  Если какие-то фикстуры в них имели подразумеваемое
  `is_machine_readable=1, scan_pages_count=0` без явного указания —
  переписать их явно, чтобы новые поля были детерминированы.
- `make lint typecheck test` зелёный.

## Definition of Done

- На фикстуре, эмулирующей CHMF-3-1913112 (`is_machine_readable=1,
  page_count=36, text_pages_count=6, scan_pages_count=30,
  reporting_standard="RSBU"`), `MetricExtractorService._build_request`
  возвращает `LLMRequest(pdf_bytes=None, user_text=<собранный
  hybrid-text>)`.
- На фикстуре, эмулирующей VTBR-3-1924077 (`page_count=43,
  text_pages_count=43, scan_pages_count=0,
  reporting_standard="RSBU"`), маршрутизация тоже идёт через text-path
  (доказывает, что `pdf_input_standards` отрезает RSBU отдельно от
  `scan_ratio_threshold`).
- На IFRS-отчёте CHMF-4-1922571 (`page_count=14, text_pages_count=13,
  scan_pages_count=1, reporting_standard="IFRS"`) маршрутизация по
  старому: `pdf_bytes is not None`.
- В `pipeline.log` событие `metric_extract_start` содержит ключи
  `scan_ratio` (float) и `pdf_input_standards` (массив).
- Конфиг `config/app.yaml` имеет новые knobs с комментариями.
- README/USER_GUIDE не требуют правок (внутренняя логика, не
  оператор-facing) — но `PIPELINE_LOGIC.md` обновлён.
