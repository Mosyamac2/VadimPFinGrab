# Промпт 19. Профили метрик: банки vs корпораты

## Цель
Разделить `metrics.yaml` на два профиля. Для **корпоратов** оставить классическую пятёрку: `revenue`, `ebitda`, `net_income`, `total_assets`, `total_debt`. Для **банков** перейти на банковские KPI: `net_interest_income`, `net_fee_income`, `net_income`, `total_assets`, `total_equity`. Профиль выбирается по полю `profile` в `tickers.yaml`.

## Контекст
- `PLAN_e-disclosure_parser_v2.md` раздел 4, Patch 19.
- ТЗ §5.2 (универсальный список метрик) и §5.4 (нормировка).
- Без этого патча для Сбера, ВТБ, Совкомбанка и т.д. в Excel-витрине будут пустые `revenue`/`ebitda` и не будут заполнены ключевые банковские строки — формальная «полнота» проходит, фактическая ценность нулевая.
- Зависит от Patch 17 (`reporting_period_*` уже в БД) и Patch 16 (Discoverer проставляет тип). Не зависит от Patch 18 (классификации страниц).

**Реальные термины, найденные в материалах `new_info/`:**

| Метрика | Тикер | Файл | Точная формулировка |
|---|---|---|---|
| `net_interest_income` | VTBR | `VTB-GO-2024_fin RUS.pdf` стр. 11, 29, 119 | «**Чистые процентные доходы**» (мн.ч.) |
| `net_interest_income` | SBER | `MSFO_3m2026.pdf` (МСФО Сбера) | «**Чистый процентный доход**» (ед.ч.) |
| `net_fee_income` | VTBR | `VTB-GO-2024_fin RUS.pdf` стр. 11, 29, 119 | «**Чистые комиссионные доходы**» (мн.ч.) |
| `net_fee_income` | SBER | `MSFO_3m2026.pdf` | «**Чистый комиссионный доход**» (ед.ч.) |
| `net_income` | VTBR / SBER | разные | «**Чистая прибыль**» |
| `total_assets` | VTBR | `VTB-GO-2024_fin RUS.pdf` стр. 122 | «**Итого активы**» |
| `revenue` | LKOH | `Бухгалтерская отчетность на 31.03.2026.pdf` стр. 4, 24 | «**Выручка**» (с кодом строки 2110) |
| `net_income` (РСБУ) | LKOH | стр. 4 | «**Чистая прибыль (убыток)**» (с кодом строки 2400) |
| `total_debt` (РСБУ) | LKOH | стр. 3 | сумма «**Заемные средства**» долгосрочных (1410) + краткосрочных (1510) |

**Важные различия диалектов:**
1. У ВТБ — формы во множественном числе («Чистые процентные доходы»), у Сбера — в единственном («Чистый процентный доход»). Список синонимов **обязан** содержать оба варианта.
2. В РСБУ долг разнесён на две строки баланса (1410 долгосрочный + 1510 краткосрочный) и подписывается «**Заемные средства**» (без буквы «ё»). Это важно для LLM-промпта.
3. EBITDA в РСБУ-документах **не публикуется** напрямую (это конструкт МСФО); в РСБУ-источнике метрика `ebitda` остаётся `null`, и **это не считается пробелом полноты** для РСБУ.

## Задачи

### 1. Перестроить `config/metrics.yaml`
Переход к структуре с профилями. Старая плоская форма больше не поддерживается.

**Правило**, которое исполнитель должен соблюсти при заполнении: **каждый синоним обязан иметь источник** в комментарии — конкретный тикер + файл + страница из `tests/fixtures/`. Если для метрики нет реальной фикстуры, подтверждающей синоним, синоним не добавляется. Список ниже — стартовая точка, проверенная на материалах `new_info/`; добавление новых синонимов в будущем требует приложить новую фикстуру. Любая «общая эрудиция» без подтверждения файлом — повод для отказа на ревью.

```yaml
profiles:
  non_bank:
    metrics:
      revenue:
        synonyms:
          - Выручка                         # подтверждено: LKOH РСБУ Q1 2026, стр. 4 (ОФР, строка 2110)
          - Выручка от реализации
          - Revenue
          - Net sales
        unit: RUB
        scale_hints: [млн руб., тыс. руб., млрд руб.]
      ebitda:
        synonyms:
          - EBITDA                          # источник в МСФО (РСБУ не содержит EBITDA напрямую — см. контекст промпта)
          - Скорректированная EBITDA
          - Adjusted EBITDA
        unit: RUB
        only_in_sources: [IFRS, ISSUER]     # для РСБUR-источника метрика остаётся null без штрафа полноты
      net_income:
        synonyms:
          - Чистая прибыль                  # подтверждено: VTB GO 2024 стр. 11
          - Чистая прибыль (убыток)         # подтверждено: LKOH РСБУ Q1 2026 стр. 4 (ОФР, строка 2400)
          - Net income
          - Прибыль за период
        unit: RUB
      total_assets:
        synonyms:
          - Итого активы                    # подтверждено: VTB GO 2024 стр. 122
          - Total assets
          - Активы
        unit: RUB
      total_debt:
        synonyms:
          - Заемные средства                # подтверждено: LKOH РСБУ Q1 2026 стр. 3 (бух. баланс, строки 1410 + 1510)
          - Заёмные средства                # тот же термин с буквой «ё», встречается в МСФО-переводах
          - Долговые обязательства
          - Total debt
          - Долгосрочные и краткосрочные кредиты и займы
        unit: RUB
        aggregation_hint: "В РСБУ это сумма строк 1410 (долгосрочные) + 1510 (краткосрочные). LLM должна суммировать обе строки баланса, если они даны раздельно."
    reporting_priority: [IFRS, RSBU, ISSUER]   # ANNUAL (type=2) — metadata-only, не источник метрик

  bank:
    metrics:
      net_interest_income:
        synonyms:
          - Чистые процентные доходы        # подтверждено: VTB GO 2024 стр. 11, 29, 119 (мн.ч.)
          - Чистый процентный доход         # подтверждено: SBER МСФО (ед.ч.)
          - Net interest income
          - Процентный доход за вычетом процентного расхода
        unit: RUB
      net_fee_income:
        synonyms:
          - Чистые комиссионные доходы      # подтверждено: VTB GO 2024 стр. 11, 29, 119 (мн.ч.)
          - Чистый комиссионный доход       # подтверждено: SBER МСФО (ед.ч.)
          - Net fee and commission income
          - Чистый доход от комиссионных операций
        unit: RUB
      net_income:
        synonyms:
          - Чистая прибыль                  # подтверждено: VTB GO 2024 стр. 11
          - Net income
          - Прибыль за период
        unit: RUB
      total_assets:
        synonyms:
          - Итого активы                    # подтверждено: VTB GO 2024 стр. 122
          - Total assets
          - Активы группы
        unit: RUB
      total_equity:
        synonyms:
          - Итого собственный капитал
          - Total equity
          - Капитал акционеров
          - Капитал, отнесенный на акционеров
        unit: RUB
    reporting_priority: [IFRS, RSBU, ISSUER]
```

**Поле `only_in_sources`** (новое в `MetricSpec`) — список стандартов, в которых метрика реально публикуется. Для всех остальных стандартов её отсутствие в выходе не учитывается в `is_incomplete`. Это позволяет корректно обрабатывать случай «РСБУ-источник + EBITDA» (РСБУ его не содержит — это не пробел полноты).

**Поле `aggregation_hint`** — текстовая подсказка, передаваемая в LLM-промпт **только** когда метрика приоритетно тянется из РСБУ. Для МСФО эта подсказка лишняя.

### 2. Конфиг-модели
В `src/edx/config/metrics_config.py`:
- Расширить `MetricSpec`:
  ```python
  class MetricSpec(BaseModel):
      synonyms: list[str]
      unit: str
      scale_hints: list[str] = []
      only_in_sources: list[Literal["IFRS","RSBU","ISSUER"]] | None = None
      aggregation_hint: str | None = None
  ```
- Добавить:
  ```python
  class MetricsProfile(BaseModel):
      metrics: dict[str, MetricSpec]
      reporting_priority: list[Literal["IFRS","RSBU","ISSUER"]]

  class MetricsConfig(BaseModel):
      profiles: dict[Literal["non_bank","bank"], MetricsProfile]
      def for_profile(self, profile: str) -> MetricsProfile: ...
  ```
- Старое поле `MetricsConfig.metrics` / `reporting_priority` удалить — конфиг ломаем намеренно, это лучше, чем «по тихому» работать со старой схемой.
- Loader (`src/edx/config/loader.py`) при чтении бросает `ConfigError` с понятным сообщением, если в YAML отсутствует `profiles:` или один из ключей профиля.

### 3. Tickers расширение
В `src/edx/config/tickers_config.py:TickerSpec` добавить поле:
```python
profile: Literal["bank","non_bank"] = "non_bank"
```
В `config/tickers.yaml.template` (и в реальном файле, который генерит Patch 20) — у каждого тикера явно указать `profile`.

### 4. Metric Extractor — выбор профиля
В `src/edx/stages/metric_extractor/service.py`:
- Принимать `tickers_config: TickersConfig` и `metrics_config: MetricsConfig`.
- Для каждой публикации читать `ticker.profile` → `metrics_config.for_profile(profile)` → использовать `profile.metrics` для построения промпта и валидации значений и `profile.reporting_priority` для сортировки документов.
- Старый код, обращавшийся к `metrics_config.metrics` напрямую, переписать.
- При построении LLM-промпта учитывать `only_in_sources`: если документ относится к стандарту, не входящему в `only_in_sources` метрики, метрика не запрашивается у LLM (экономит токены и снимает «галлюцинации» вида «EBITDA из РСБУ»).
- Если у метрики есть `aggregation_hint` И источник = `RSBU`, добавить hint в системную часть промпта в формате `«<metric_key>: <hint>»`. Это даёт LLM правильно агрегировать строки баланса.

### 5. Excel-схема
В `src/edx/stages/writer/excel.py`:
- Лист `metrics` остаётся в формате long: `(ticker, period, metric, value, unit, source, confidence)`. Перечень метрик — объединение профилей (метрики обоих профилей могут встречаться в одном листе).
- В лист `tickers` добавить колонку `profile`.
- В QA-отчёте `qa_report` `is_incomplete` считается относительно метрик профиля, к которому привязан тикер.

### 6. Тесты (мульти-эмитент через реальные термины)
- `test_metrics_config_loads_two_profiles`: чтение `config/metrics.yaml` → `for_profile("bank")` возвращает 5 метрик с банковскими ключами, `for_profile("non_bank")` — корпоративную пятёрку.
- `test_metrics_config_rejects_legacy_flat_yaml`: подсунуть старую плоскую схему → `ConfigError`.
- `test_synonyms_cover_real_documents`: параметризованный тест по фикстурам:
  - SBER MSFO: должен содержать `Чистый процентный доход` (ед.ч.) — синоним `net_interest_income` найден в тексте.
  - VTB GO 2024: должен содержать `Чистые процентные доходы` (мн.ч.) — синоним `net_interest_income` найден в тексте.
  - LKOH РСБУ Q1 2026: должен содержать `Выручка`, `Чистая прибыль (убыток)`, `Заемные средства` — синонимы `revenue`, `net_income`, `total_debt` найдены в тексте.
  Тест читает `config/metrics.yaml` и грепает реальный текст PDF из `tests/fixtures/pdf/` — каждый «обещанный» синоним должен встречаться хотя бы в одном из документов профиля. Тест предотвращает регрессию вида «кто-то удалил синоним для VTB и не заметил».
- `test_metric_extractor_uses_bank_profile_for_sber`: моковая публикация с `ticker=SBER` (profile=bank); промпт LLM содержит `net_interest_income`, не содержит `revenue`/`ebitda`.
- `test_metric_extractor_uses_nonbank_profile_for_lkoh`: симметрично, для LKOH (profile=non_bank).
- `test_metric_extractor_skips_ebitda_for_rsbu_source`: моковая публикация LKOH РСБУ; промпт LLM **не** содержит `ebitda` (отфильтровано через `only_in_sources`).
- `test_metric_extractor_includes_aggregation_hint_for_total_debt_rsbu`: моковая публикация LKOH РСБУ; в промпте LLM присутствует подсказка про сумму строк 1410+1510.
- `test_validator_completeness_uses_profile_metrics_minus_only_in_sources`: при `is_incomplete` считается покрытие по 5 метрикам соответствующего профиля, но `ebitda` не учитывается, если источник = РСБУ.
- `test_writer_tickers_sheet_has_profile_column`.

## Definition of Done
- `config/metrics.yaml` загружается под новой схемой `profiles:`.
- Запуск `edx update --ticker SBER` (банк) генерирует строки `net_interest_income`, `net_fee_income` и не пытается требовать `revenue`/`ebitda`.
- Запуск `edx update --ticker LKOH` (корпорат) сохраняет старое поведение.
- Excel `tickers` лист содержит колонку `profile`.
- `make lint typecheck test` зелёные.
