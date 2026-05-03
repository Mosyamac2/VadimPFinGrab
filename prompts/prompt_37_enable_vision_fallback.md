# Промпт 37. Включить vision_fallback по умолчанию + консервативный cap

## Цель

Включить `vision_fallback_enabled: true` в `config/app.yaml`, чтобы
запустить инфраструктуру Patch 33 для немногих оставшихся «hard
case» публикаций. Run_id=14 показал ровно один такой кейс:

- `SBER-3-1881246` (Сбербанк, РПБУ Q1 2025, МСА 805 — упрощённая
  отчётность, 11-страничный scan-only PDF) — coverage_ratio = 0.4
  (`extracted: 2, requested: 5`). Tesseract @ 400 DPI + PSM 6 +
  retry (Patch 31) вытащил `revenue` и `net_income`, но потерял
  `total_assets`, `total_equity`, `net_fee_income`.

Anthropic vision на 11 image-страницах с грид-таблицей балансовой
формы 0710001 справляется значительно лучше Tesseract'а. Token
бюджет: ~1500 vision-tokens × 8 страниц = 12 000 токенов на
fallback × ~1 публикация на прогон = $0.04/run при $3/M input.
Приемлемо.

## Контекст

- Patch 33 (vision_fallback) уже в master с дефолтом `false`. Patch
  37 — это просто **переключение флага** + сужение
  `vision_fallback_max_pages` с 12 до 8 (более консервативный cap).
- Никакого нового кода не требуется — вся ветвь `_extract_one`,
  retry, merge через Patch 26 dedup уже на месте.
- **Зависимости:** Patch 33 (имеется), Patch 29 (для маршрутизации
  RSBU в text-path первым), Patch 30 (balance-anchor trim), Patch 31
  (улучшенный Tesseract baseline).

## Задачи

### 1. `config/app.yaml`

Под `metric_extractor:` (где уже есть Patch 33 knobs):

```yaml
# Patch 37: enable vision-fallback by default. Run-14 baseline showed
# 1 publication (SBER-3-1881246) at 40% coverage after Patches 29-31;
# vision-fallback retry on its 8 scan pages costs ~$0.04 per run and
# lifts coverage to expected 80%+. Cap reduced 12→8 to keep cost
# predictable across the catalogue (worst-case 12000 tokens/pub).
vision_fallback_enabled: true
vision_fallback_threshold: 0.5
vision_fallback_max_pages: 8
```

(Эти ключи уже описаны в файле — править только значения двух
строк.)

### 2. Тесты

Существующие тесты `tests/stages/metric_extractor/test_vision_fallback.py`
не изменяются — они уже покрывают вариант `enabled=True`.

В `tests/config/test_models.py` (или существующий
`test_loading.py`) — добавить test:

- `test_app_yaml_has_vision_fallback_enabled_by_default`: загрузка
  `config/app.yaml` через `AppSettings` возвращает
  `app.metric_extractor.vision_fallback_enabled is True` и
  `vision_fallback_max_pages == 8`. Это sentinel-проверка против
  случайного отката флага в будущем.

### 3. `USER_GUIDE.md`

В разделе «Какие YAML можно править» — строку про `app.yaml`
расширить: «`metric_extractor.vision_fallback_enabled` (по
умолчанию **true**) — включает retry с Anthropic vision, когда
text-pass дал coverage < 0.5. На прогон добавляет ~$0.04 за каждую
проблемную публикацию».

В разделе «Стоимость в эксплуатации» обновить оценку: «Anthropic
API (LLM) | $5–35 в зависимости от количества эмитентов и
проблемных публикаций (vision-fallback при coverage<50%)».

### 4. `PIPELINE_LOGIC.md`

§5.7 (Метрики наблюдаемости) — добавить упоминание, что после
Patch 37 в `pipeline.log` для проблемных публикаций будут
дополнительные события `metric_extract_vision_fallback_triggered` и
`metric_extract_vision_fallback_completed` с
`coverage_before/coverage_after` парой.

В §11 (debug) — строку «`coverage_ratio < 0.5` после text-pass
для scan-only RSBU → vision-fallback должен сработать
автоматически; проверить `metric_extract_vision_fallback_completed`
в логах для этой публикации».

## Тесты, которые должны проходить

- Новый sentinel-тест зелёный.
- Существующие тесты vision_fallback не сломаны.
- `make lint typecheck test` зелёный.

## Definition of Done

- `config/app.yaml` содержит `vision_fallback_enabled: true` и
  `vision_fallback_max_pages: 8`.
- На реальной публикации `SBER-3-1881246` после следующего
  `edx run --full-reload`:
  - В `pipeline.log` появляются события
    `metric_extract_vision_fallback_triggered` (с
    `primary_coverage: 0.4`) и
    `metric_extract_vision_fallback_completed` (с
    `coverage_after >= 0.6`).
  - Публикация `is_incomplete=0` в state.sqlite.
- На IFRS-публикациях с coverage>=0.5 fallback **не** запускается
  (т.е. number of LLM calls per such publication остаётся 1).
- `USER_GUIDE.md` + `PIPELINE_LOGIC.md` обновлены.

## Заметка по cost-control

Если оператор увидит, что vision-fallback запускается слишком часто
(>5 публикаций на прогон), это сигнал, что либо Tesseract baseline
просел (стоит поднять `tesseract_dpi` до 600), либо качество новых
эмитентов хуже, чем предполагалось. В этом случае можно либо:

- Снизить `vision_fallback_threshold` до 0.3 (фолбэк только при
  очень плохом первом проходе), или
- Временно переключить `vision_fallback_enabled: false` и провести
  отдельный анализ.

Глобальный kill switch для per-ticker vision-only path
(`vision_only_global_disabled`, Patch 34) **не** влияет на этот
fallback — это разные механизмы.
