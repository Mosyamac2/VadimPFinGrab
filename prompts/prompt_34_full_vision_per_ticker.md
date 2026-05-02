# Промпт 34. Full vision-path как opt-in per-ticker (последняя инстанция)

## Цель

Дать оператору рычаг для эмитентов, у которых даже после Patch 29 +
30 + 31 + 32 + 33 coverage остаётся неприемлемо низким. Это **редкий
opt-in**, не дефолт; задумывается как «закрыть последний хвост из
1–2 эмитентов до того, как мы будем готовы инвестировать в cloud-OCR
(Yandex Vision / Google Vision)».

Принцип: для конкретного тикера в `tickers.yaml` ставится флаг
`use_vision_extraction: true`, и тогда Metric Extractor для **всех**
RSBU/IFRS публикаций этого эмитента идёт через image-input в
Anthropic — каждая страница рендерится в PNG (через `pdf2image`),
склеивается в multimodal-message, агрегируется по ответам.
Tesseract / native PDF-input игнорируется.

Это самый дорогой путь (~1500 vision-tokens на страницу). На 50
публикаций × 30 страниц × 1500 токенов ≈ 2.25 млн токенов. Тариф
$3/M на input → $7 за прогон по одному «vision-only» эмитенту.
Поэтому — opt-in, явно, и с предупреждением в `tickers.yaml.template`.

## Контекст

- Patch 34 имеет смысл **только если Patch 33 не справился**. Если
  vision-fallback на scan-страницах даёт coverage ≥ 0.7, делать
  full vision на всех страницах избыточно.
- На IFRS-документах Anthropic native-PDF и так работает отлично
  (см. live-прогон: VTBR-4-1880207 coverage=1.0 при input_tokens
  305k). Patch 34 для IFRS бессмыслен — туда возвращаемся к
  стандартному PDF-пути даже при флаге `use_vision_extraction:
  true`. Флаг применяется **только к RSBU и ISSUER**.
- **Зависимости:** Patch 29 (для маршрутизации), Patch 33 (общая
  инфраструктура vision-input через LLMRequest). Без них Patch 34
  технически работать не будет.

## Задачи

### 1. `src/edx/config/tickers_config.py`

Добавить в `TickerEntry`:

```python
use_vision_extraction: bool = False
```

В `tickers.yaml.template` добавить пример с комментарием:

```yaml
  - ticker: <TICKER>
    name: <НАЗВАНИЕ>
    e_disclosure_id: "REPLACE_ME"
    profile: bank | non_bank
    inn: "REPLACE_ME"
    # Patch 34: opt-in. Включать ТОЛЬКО если Patches 29-33 не дали
    # coverage_ratio > 0.5 для этого эмитента. Каждая RSBU/ISSUER
    # публикация будет стоить ~$7 на vision-input.
    # use_vision_extraction: false
```

В реальный `config/tickers.yaml` ничего не вписывать (флаг остаётся
false для всех).

### 2. `src/edx/providers/llm/base.py`

`LLMRequest` обогащается ещё одним полем (Patch 33 уже добавил
`pdf_page_indices`):

```python
# Patch 34: список pre-rendered страниц как PNG image bytes.
# Если задан — провайдер шлёт каждый item как content block
# {"type": "image", ...}. pdf_bytes / pdf_page_indices игнорируются.
pdf_page_images: tuple[bytes, ...] | None = None
```

### 3. `src/edx/providers/llm/anthropic_provider.py`

Расширить `_build_user_content`:

```python
def _build_user_content(self, req: LLMRequest) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if req.pdf_page_images:
        for image_bytes in req.pdf_page_images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                },
            })
    elif req.pdf_bytes is not None:
        # ... (Patch 33 path: document с slicing)
    content.append({"type": "text", "text": req.user_text})
    return content
```

`pdf_page_images` имеет приоритет над `pdf_bytes` (это явный сигнал
оператора «иди через vision»).

### 4. `src/edx/stages/metric_extractor/service.py`

В `_build_request` добавить ветку:

```python
ticker_entry = self.tickers_config.find(pub.ticker)
use_vision = (
    ticker_entry is not None
    and ticker_entry.use_vision_extraction
    and standard in ("RSBU", "ISSUER")
    and full_path.is_file()
)

if use_vision:
    return self._build_vision_request(pub, chosen, profile_name, standard)

# ... (Patches 29 + 33 — обычный путь)
```

`_build_vision_request`:

1. Открыть PDF через `pymupdf`, отрендерить каждую страницу в PNG
   (300 DPI — баланс между качеством и vision-token-cost; больше
   не нужно: Anthropic vision сам upscale'ит).
2. Cap на `vision_max_pages_per_request` (default 25 — Anthropic
   принимает до 100 image content blocks, но за ценой).
3. Если страниц больше — режем приоритетно (после Patch 30 знаем
   индекс «БУХГАЛТЕРСКИЙ БАЛАНС»; берём от него `+12` страниц
   назад на ОФР/баланс/капитал/денежные потоки).
4. Сборка `LLMRequest(pdf_page_images=tuple(images), pdf_bytes=None,
   user_text="..." )`.
5. user_text содержит ту же подсказку, что и для обычного RSBU
   text-path — «извлекай числа только из форм отчётности».

Лог:

- `metric_extract_vision_only_request` с числом страниц и оценкой
  токенов.

### 5. Excel-витрина

В листе `tickers` (Patch 19) добавить колонку
`use_vision_extraction` (bool). Пользователь сразу видит, какие
эмитенты на дорогом пути.

`src/edx/stages/writer/excel.py` — `TickerExportRow` и
`TICKERS_HEADERS` обновить.

### 6. `src/edx/config/app_config.py`

Глобальная защита (на случай, если оператор включит флаг сразу для 30
тикеров):

```python
class MetricExtractorConfig(BaseModel):
    ...
    vision_only_max_pages_per_request: int = Field(default=25, ge=1, le=100)
    vision_only_global_disabled: bool = False  # kill switch
```

### 7. `config/app.yaml`

```yaml
metric_extractor:
  ...
  # Patch 34: глобальный kill switch для vision-only path.
  # Если true — флаг tickers.use_vision_extraction игнорируется
  # (все идут через обычный путь). Полезно, если внезапно начнут
  # биллиться большие суммы.
  vision_only_global_disabled: false
  vision_only_max_pages_per_request: 25
```

### 8. Тесты

`tests/providers/llm/test_anthropic.py`:

- `test_pdf_page_images_emits_image_content_blocks`: фейковый
  request с тремя PNG-байтами → Anthropic mock получает три
  `{"type": "image", ...}` content blocks плюс один text block.
  Проверка через захват kwargs.
- `test_pdf_page_images_takes_precedence_over_pdf_bytes`: задано
  и то и то → шлём image, document не отправляется.

`tests/stages/metric_extractor/test_vision_only.py`:

- `test_flag_disabled_uses_normal_path`: ticker с
  `use_vision_extraction=False` → request без `pdf_page_images`.
- `test_flag_enabled_for_rsbu_uses_vision_path`: ticker с
  `use_vision_extraction=True`, standard="RSBU" → request с
  `pdf_page_images` len > 0.
- `test_flag_enabled_for_ifrs_skips_vision_path`: ticker с
  флагом, standard="IFRS" → IFRS остаётся на обычном PDF-пути.
- `test_global_kill_switch_overrides_ticker_flag`: оба флага
  включены, но `vision_only_global_disabled: true` → обычный
  путь.
- `test_max_pages_cap_truncates`: 40-страничный PDF,
  `max_pages=25` → ровно 25 PNG в request.
- `test_excel_tickers_sheet_includes_vision_column`: Excel-writer
  с тикером с флагом → лист `tickers` имеет колонку и значение
  `True`.

### 9. `USER_GUIDE.md`

Новый раздел перед «Когда нужна помощь разработчика»: «Vision-only
для тяжёлых эмитентов (Patch 34)». Содержание: что это, когда
включать, как оценить стоимость, kill switch.

В разделе «Какие YAML можно править» строку про `tickers.yaml`
расширить: можно поставить `use_vision_extraction: true` — но только
после проверки эмитента на normal-пути.

### 10. `PIPELINE_LOGIC.md`

Добавить §5.9 «Full vision path (Patch 34, opt-in)» с описанием
маршрутизации, бюджета, kill switch.

## Тесты, которые должны проходить

- Все 2 + 6 = 8 новых тестов зелёные.
- Существующие тесты не сломаны (Patches 29/33 продолжают работать
  без флага).
- `make lint typecheck test` зелёный.

## Definition of Done

- На фейковом ticker'е CHMF с `use_vision_extraction: true` Metric
  Extractor для RSBU-публикации шлёт vision-only request со всеми
  страницами (или капом).
- Тот же ticker для IFRS-публикации продолжает идти через обычный
  PDF-путь.
- Глобальный kill switch выключает vision-only одной строкой в
  app.yaml, не трогая tickers.yaml.
- Excel-лист `tickers` содержит колонку `use_vision_extraction`.
- Документация (USER_GUIDE + PIPELINE_LOGIC) объясняет, когда
  включать, как оценить cost, как откатить.

## Заметка по приоритетам

Patch 34 — **«не трогать пока не понадобится»**. Перед его
реализацией:

1. Прогнать `edx run --full-reload` после Patches 29 + 30 + 31 + 32.
2. Сравнить coverage до/после в Excel.
3. Если ≥ 95% эмитентов имеют `is_incomplete=0` — Patch 34 лежит на
   полке. Перейти к Patch 33 только для оставшихся 1-2 эмитентов.
4. Если Patch 33 закрывает их — Patch 34 не нужен совсем.
5. Иначе — реализовать Patch 34 как последнюю меру.
