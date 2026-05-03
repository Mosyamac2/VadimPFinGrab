# Промпт 35. Issuer-trim — минимальная длина среза + детекция TOC-only matches

## Цель

Закрыть критическую регрессию из run_id=14: для IZNM (Ижнефтемаш — малый
эмитент, 3 публикации Issuer Report) `extract_section_1_4` (Patch 21)
возвращал срез длиной **181 символ** — это была единственная строка
оглавления вида «1.4 Основные финансовые показатели … 14», а не сам
раздел. LLM получал почти пустой контекст и эмитировал coverage_ratio
0.0 для всех трёх публикаций.

Лог-симптом:

```
metric_extract_issuer_section_extracted ... chars: 181
metric_extract_completed ... extracted: 0, requested: 5, coverage_ratio: 0.0
```

Корень: `ANCHOR_START.finditer(text)` находит **2** вхождения в TOC
(одно в начале, одно сразу после), Patch 21 берёт «last match» — но
обе TOC-строки указывают на одно и то же место, и slice до anchor 1.5
получается крошечным (между «1.4 ... 14» и «1.5 ... 18» 181 символ).

Patch 35 добавляет два дешёвых safeguard'а:

1. **Min-length validation:** если `len(content) < min_section_chars`
   после trim → вернуть `content=None` и warning, чтобы Metric
   Extractor сделал fall-back на полный текст. Лучше шумный полный
   контекст, чем 181-байтная заглушка.

2. **TOC-only detection:** если найдено ≥ 2 anchor matches и
   расстояние между ними < `toc_distance_chars` (default 3000), оба
   считаются TOC-mention'ами, ни один не используется → `content=None`.

## Контекст

- Файл: `src/edx/stages/text_extractor/issuer_trim.py`. Текущая
  реализация Patch 21 уже умеет fall-back на полный текст при
  `content=None` — Patch 35 просто расширяет случаи, когда `None`
  возвращается.
- В `MetricExtractorService._assemble_user_text` (Patch 21) уже есть
  ветка «`trim.content is None` → keep doc_text untouched + emit
  warning» — она нам и нужна.
- **Зависимости:** работает только с Patch 21 (имеется в master).
  Не зависит от Patch 36/37.

## Задачи

### 1. `src/edx/stages/text_extractor/issuer_trim.py`

Текущая сигнатура:

```python
def extract_section_1_4(text: str, *, max_chars: int) -> SectionExtract:
```

Расширить:

```python
def extract_section_1_4(
    text: str,
    *,
    max_chars: int,
    min_section_chars: int = 500,
    toc_distance_chars: int = 3000,
) -> SectionExtract:
```

Логика:

1. После того как нашли все matches, **до** взятия `last`, проверить:
   если `len(matches) >= 2` и
   `(matches[-1].start() - matches[0].start()) < toc_distance_chars`
   → оба считаются TOC, вернуть `content=None,
   warnings=("section_1_4_only_in_toc",)`.

2. После того как обрезали `content = text[start_pos:end_pos]` и
   применили `max_chars`, проверить: если
   `len(content) < min_section_chars` → вернуть `content=None,
   warnings=("section_1_4_too_short",)`.

3. Существующие warnings (multi-match, end-anchor missing,
   max_chars truncated) сохранить.

Не менять public API beyond добавления опциональных параметров с
дефолтами.

### 2. `src/edx/stages/metric_extractor/service.py`

В `__init__` добавить параметр (мирорит уже существующий
`issuer_trim_max_chars`):

```python
issuer_trim_min_section_chars: int = 500,
issuer_trim_toc_distance_chars: int = 3000,
```

В `_assemble_user_text`, в ветке `if standard == "ISSUER":` —
прокинуть оба параметра в `extract_section_1_4(...)`.

### 3. `src/edx/config/app_config.py`

В `TextExtractorConfig` добавить (рядом с `issuer_trim_max_chars`):

```python
issuer_trim_min_section_chars: int = Field(default=500, ge=0)
issuer_trim_toc_distance_chars: int = Field(default=3000, ge=100)
```

### 4. `src/edx/stages/metric_extractor/factory.py`

Прокинуть оба параметра из `settings.app.text_extractor` в
`MetricExtractorService(...)`.

### 5. `config/app.yaml`

Под `text_extractor:` (где уже есть `issuer_trim_max_chars: 30000`)
добавить:

```yaml
# Patch 35: when extract_section_1_4 returns a slice shorter than this,
# treat it as a TOC-only false-positive and fall back to the full
# document text. Tuned for small issuers (IZNM, etc.) whose Issuer
# Reports have a 1.4 entry in the TOC but no clearly-delimited body
# section under that heading.
issuer_trim_min_section_chars: 500
# Patch 35: when the section 1.4 anchor matches twice within this many
# characters of each other, both are treated as TOC mentions (table-
# of-contents typically lists 1.4 once and the next section 1.5
# directly after — < 3000 chars between them).
issuer_trim_toc_distance_chars: 3000
```

### 6. Тесты

В `tests/stages/text_extractor/test_issuer_trim.py` (или новый):

- `test_short_slice_returns_none`: synthetic input where matches are
  legit but the slice between 1.4 and 1.5 is < 500 chars → `content
  is None`, `warnings == ("section_1_4_too_short",)`.
- `test_two_close_matches_flagged_as_toc_only`: two matches 200 chars
  apart → `content is None`,
  `warnings == ("section_1_4_only_in_toc",)`.
- `test_two_distant_matches_uses_last`: two matches 5000 chars apart
  (TOC + real heading) → still uses last match (existing Patch 21
  behaviour).
- `test_long_section_below_max_chars_still_returns_content`: legit
  10k-char section between 1.4 and 1.5 → returns the slice
  unchanged.
- `test_min_section_chars_threshold_boundary`: section exactly
  `min_section_chars` long → returned (≥ threshold). One char
  shorter → `None`.

В `tests/stages/metric_extractor/test_balance_anchor_routing.py`
(или новый `test_issuer_trim_routing.py`):

- `test_issuer_text_path_falls_back_on_too_short_slice`: фикстура
  где раздел 1.4 имеет TOC-line на 200 символов; service шлёт
  полный текст в LLM (а не пустой); `metric_extract_issuer_trim`
  warning `section_1_4_too_short` присутствует.

### 7. `PIPELINE_LOGIC.md`

§5.4 — в блок про ISSUER trim добавить упоминание
`min_section_chars` и `toc_distance_chars` с короткой мотивацией.
В §11 (debug checklist) — строка «coverage_ratio=0 на ISSUER
с очень коротким `chars` в `metric_extract_issuer_section_extracted`
→ Patch 35 должен был fall-back; проверить что
`section_1_4_too_short` warning эмитируется».

## Тесты, которые должны проходить

- 5 + 1 = 6 новых тестов зелёные.
- Существующие тесты Patch 21 (`test_issuer_trim.py`) не сломаны.
- `make lint typecheck test` зелёный.

## Definition of Done

- На фейковом ISSUER-документе с TOC-only анкором (две match'а на
  расстоянии 200 chars) `extract_section_1_4` возвращает
  `content=None`.
- На синтетической фикстуре «TOC + 200-байт раздел 1.4 + раздел
  1.5» Metric Extractor шлёт LLM **полный текст**, не 200-байтную
  заглушку.
- На реальной IZNM-3 (`IZNM-5-1897480/824/1925636`) после следующего
  `edx run --full-reload` coverage_ratio > 0.5 для всех трёх
  публикаций (ожидаемо ~60-80% — full text 20-26 страниц достаточен
  для 5 базовых KPI).
- `PIPELINE_LOGIC.md` обновлён.
