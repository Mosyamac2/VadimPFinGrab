# Промпт 33. Vision-fallback при низком coverage (опционально)

## Цель

Дать Metric Extractor «второй шанс» для публикаций, где первый
text-extract проход дал `coverage_ratio < threshold`. После Patch 29 +
30 + 31 + 32 ожидается, что большинство RSBU-форм поднимутся до
0.7–1.0 coverage. Но какие-то особо тяжёлые сканы (плохое качество
печати, наклон страницы, большая подпись поверх цифры) останутся.
Для них — vision-retry через **Anthropic native PDF input**, но
**не всем документом**, а только страницами, помеченными как `scan`
в `documents.pages_classification`. Это держит token-budget
управляемым и стреляет точно по проблемному содержимому.

Patch — **opt-in через флаг `metric_extractor.vision_fallback:
false`**. Дефолт false: пользователь (или мы при тюнинге) включит,
если P0–P2 не дотянули.

## Контекст

- Патч **становится осмысленным только после Patch 29**: без него
  Metric Extractor и так шлёт PDF целиком, и vision-fallback избыточен.
- Стоимость: vision-input в Anthropic — ~1500 input-токенов на
  страницу + цена text output. На 5 публикаций × 8 scan-страниц
  × 1500 = 60 000 токенов экстра. На тарифе 450k ITPM это
  10–15% месячного бюджета — приемлемо как fallback, не как дефолт.
- **Зависимости:** Patch 29 (для маршрутизации), Patch 30
  (для качества первого прохода). От Patch 31/32 не зависит, но
  выгоду даёт суммарную.

## Задачи

### 1. `src/edx/providers/llm/base.py`

`LLMRequest` сейчас несёт `pdf_bytes: bytes | None = None`. Нужно
расширить контракт, чтобы можно было передать **набор номеров
страниц**, а не весь PDF.

Вариант A (простой): добавить поле `pdf_page_indices: tuple[int,
...] | None = None`. Если задано — провайдер должен слать только
указанные страницы.

```python
class LLMRequest(BaseModel):
    ...
    pdf_bytes: bytes | None = None
    # Patch 33: если non-null — провайдер шлёт только эти 0-based
    # страницы из pdf_bytes. Остальные опускаются. None означает
    # «весь документ» (legacy-поведение).
    pdf_page_indices: tuple[int, ...] | None = None
```

`LLMResponse` без изменений.

### 2. `src/edx/providers/llm/anthropic_provider.py`

В `_build_user_content` обработать `req.pdf_page_indices`:

```python
def _build_user_content(self, req: LLMRequest) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if req.pdf_bytes is not None:
        if req.pdf_page_indices:
            sliced = _slice_pdf_pages(req.pdf_bytes, req.pdf_page_indices)
        else:
            sliced = req.pdf_bytes
        content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(sliced).decode("ascii"),
            },
        })
    content.append({"type": "text", "text": req.user_text})
    return content
```

Хелпер `_slice_pdf_pages(pdf_bytes, indices)`:

```python
def _slice_pdf_pages(pdf_bytes: bytes, indices: tuple[int, ...]) -> bytes:
    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    dst = pymupdf.open()
    try:
        for idx in indices:
            dst.insert_pdf(src, from_page=idx, to_page=idx)
        out = io.BytesIO()
        dst.save(out)
        return out.getvalue()
    finally:
        src.close()
        dst.close()
```

(`pymupdf` уже зависимость через Text Extractor, повторно ставить не
нужно.)

### 3. `src/edx/providers/llm/openrouter_provider.py`

OpenRouter PDF не поддерживает (`supports_pdf_input=False`). Если
`pdf_bytes is not None` — игнорируем как и раньше (warning
`openrouter_pdf_input_dropped`). `pdf_page_indices` — тоже игнор.
Контракт сохранён.

### 4. `src/edx/stages/metric_extractor/service.py`

Добавить retry-логику в `MetricExtractorService.run` (или в
`_extract_one`, в зависимости от структуры). Псевдокод:

```python
outcome = self._extract_one_attempt(pub, chosen, profile, standard)
if (
    self.vision_fallback_enabled
    and outcome.coverage_ratio < self.vision_fallback_threshold
    and primary_doc.scan_pages_count > 0
    and self.llm_provider.supports_pdf_input
    and full_path.is_file()
):
    self._log.info(
        "metric_extract_vision_fallback_triggered",
        publication_id=pub.publication_id,
        primary_coverage=outcome.coverage_ratio,
        scan_pages=primary_doc.scan_pages_count,
    )
    outcome = self._extract_one_vision_fallback(pub, chosen, ...)
return outcome
```

`_extract_one_vision_fallback`:

1. Парсит `primary_doc.pages_classification` (JSON-string),
   выбирает 0-based индексы страниц с `kind="scan"`.
2. Cap на максимум `vision_fallback_max_pages` страниц (default
   12) — рубим, если сканов слишком много (например, 30 из
   `FS_31122025.pdf`); первые N страниц после якоря баланса
   приоритетнее, чем хвост документа.
3. Строит `LLMRequest(... pdf_bytes=full_path.read_bytes(),
   pdf_page_indices=tuple(scan_indices) ...)`.
4. Прогоняет через `llm_provider.complete()`.
5. Прогоняет тот же post-processing (Pydantic, drop comparative,
   dedup) и пишет в БД ОТДЕЛЬНУЮ строку с
   `extraction_method='vision_fallback'`.

Опционально (более простой первый шаг): не писать как отдельную
строку, а **слить** результаты двух проходов — если первый дал
revenue=null, а второй дал revenue=123.4 → берём второй. Реализуется
через тот же dedup-словарь, что в `_build_metric_rows` (Patch 26),
с приоритетом «non-null wins, и vision_fallback — wins of last
resort, не overrides existing non-null».

Рекомендация — начать с слияния (проще, не требует новой колонки
в `metrics`).

Лог события:

- `metric_extract_vision_fallback_triggered` — перед запуском.
- `metric_extract_vision_fallback_completed` — с
  `coverage_before, coverage_after, vision_pages, vision_input_tokens`.

### 5. `src/edx/config/app_config.py`

В `MetricExtractorConfig`:

```python
vision_fallback_enabled: bool = False
vision_fallback_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
vision_fallback_max_pages: int = Field(default=12, ge=1, le=50)
```

### 6. `config/app.yaml`

```yaml
metric_extractor:
  ...
  # Patch 33: повторный заход в LLM с vision-input на scan-страницах,
  # если первый text-pass дал coverage_ratio ниже vision_fallback_threshold.
  # Дорогой fallback (~1500 input tokens на страницу × N страниц);
  # включать после того, как Patches 29-31 уже сделаны и Excel смотрит
  # «почти полностью», но 1-2 публикации застряли.
  vision_fallback_enabled: false
  vision_fallback_threshold: 0.5
  vision_fallback_max_pages: 12
```

### 7. Тесты

`tests/providers/llm/test_anthropic.py`:

- `test_pdf_page_indices_slices_input`: фейковый
  `pymupdf`-документ из 5 страниц, `pdf_page_indices=(1, 3)` →
  Anthropic mock получает PDF с 2 страницами. Проверка через
  захват `messages.create.kwargs["messages"][0]["content"]`.
- `test_pdf_page_indices_none_passes_full_pdf`: `pdf_page_indices
  is None` → весь документ, как раньше.

`tests/stages/metric_extractor/test_vision_fallback.py`:

- `test_disabled_by_default`: `vision_fallback_enabled=False` →
  даже при coverage=0 retry не запускается.
- `test_triggered_on_low_coverage`: первый проход возвращает
  coverage=0.0, retry — coverage=0.75. Проверка: финальный
  outcome имеет coverage=0.75 и в логе есть
  `metric_extract_vision_fallback_triggered`.
- `test_skipped_when_no_scan_pages`: `scan_pages_count=0` →
  vision-fallback не запускается (нечего OCR-ить).
- `test_skipped_when_provider_does_not_support_pdf`: provider с
  `supports_pdf_input=False` → fallback не запускается.
- `test_max_pages_cap_truncates`: 30 scan-страниц,
  `vision_fallback_max_pages=12` → в LLM уходит 12 страниц.
- `test_merge_non_null_wins`: первый проход → revenue=null, retry
  → revenue=123.0. Финальная строка в metrics имеет revenue=123.0.

### 8. `PIPELINE_LOGIC.md`

Добавить новый §5.8 «Vision-fallback (Patch 33)» с описанием
триггера, дозы и стоимости.

В §11 («Чек-лист отладки») добавить строку: «coverage стабильно
0.5–0.7 → попробовать `vision_fallback_enabled: true` и сравнить
до/после».

## Тесты, которые должны проходить

- Все 2 + 6 = 8 новых тестов зелёные.
- Существующие тесты Anthropic-провайдера и Metric Extractor не
  сломаны.
- `make lint typecheck test` зелёный.

## Definition of Done

- На фикстуре, эмулирующей FS_31122025.pdf после Patch 29 первого
  прохода (coverage=0.0), включение `vision_fallback_enabled:
  true` приводит к коду, в котором запрос идёт в Anthropic с
  `pdf_page_indices=(7,8,9,10,…,18)` (только scan-страницы) и
  возвращает coverage > 0.5.
- Без флага (default) поведение пайплайна остаётся идентичным
  Patch 29 + 30 + 31 — никаких лишних вызовов LLM.
- В `pipeline.log` события `metric_extract_vision_fallback_*`
  присутствуют, когда фолбэк сработал.
- Cost-budget удерживается: `vision_input_tokens` ≤ `1500 ×
  vision_fallback_max_pages` (контроль через сумму
  `cache_creation_input_tokens` + `input_tokens` в логах).
- `PIPELINE_LOGIC.md` обновлён.
