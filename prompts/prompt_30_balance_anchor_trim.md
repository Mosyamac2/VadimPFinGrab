# Промпт 30. Отрезать аудиторское заключение от формы РСБУ перед LLM

## Цель

Сократить «шум до полезной нагрузки» на входе Metric Extractor для
РСБУ-документов, начинающихся с многостраничного аудиторского
заключения (Кэпт / Б1 / ДРТ / Делойт). Реальный пример —
`CHMF-3-1913112` / `FS_31122025.pdf`: страницы 1–7 содержат текст
аудиторского заключения, страницы 8+ — сканированные формы баланса и
ОФР. После Patch 29 этот документ идёт через text-extract path и
получает `user_text` ~86 615 символов, из которых ~30 000 — текст
аудиторского отчёта (включая подробности тестирования на обесценение,
ключевые вопросы аудита и т.п.). Эти данные:

1. Не несут KPI и могут запутать LLM (фразы «прибыль 100 млн» в
   контексте обоснования аудиторской процедуры — настоящий
   риск hallucination).
2. Тратят токены и cap'ят полезный контент сканов через
   `text_extractor.max_chars` (default 400 000).
3. Уменьшают prompt-cache hit ratio — каждый аудитор пишет
   уникальный preamble, и system-prompt тут ни при чём.

Patch 30 добавляет аналог `extract_section_1_4` для ISSUER (Patch 21):
функцию `extract_balance_sheet_onwards`, которая ищет якорь начала
балансовой формы и обрезает всё до него. Применяется только для
RSBU-источника.

## Контекст

- Якорь — устойчивая фраза «БУХГАЛТЕРСКИЙ БАЛАНС» / «Бухгалтерский
  баланс» / `Форма по ОКУД 0710001` (код формы Минфина для РСБУ-баланса).
  Эти три варианта закрывают живые публикации e-disclosure 2023–2026.
- IFRS-документы трогать **нельзя**: у консолидированной отчётности
  по МСФО нет встроенного аудиторского preamble (он идёт отдельным
  файлом), и формы называются `Consolidated Statement of Financial
  Position`, не «БУХГАЛТЕРСКИЙ БАЛАНС».
- ISSUER уже обрезается отдельным механизмом
  (`extract_section_1_4`) — Patch 30 его не трогает.
- **Зависимости:** опирается на Patch 29 (без Patch 29 RSBU-тексты
  всё равно не доходят до Metric Extractor через text-path); если
  Patch 29 не задеплоен, Patch 30 эффекта не даст, но и не сломает
  ничего.

## Задачи

### 1. `src/edx/stages/text_extractor/balance_anchor.py` (новый файл)

Зеркало `issuer_trim.py`. Публичный API — одна функция:

```python
@dataclass(frozen=True)
class BalanceTrimResult:
    content: str | None
    anchor_label_seen: str | None
    warnings: tuple[str, ...]


def extract_balance_sheet_onwards(
    text: str,
    *,
    max_chars: int = 200_000,
) -> BalanceTrimResult:
    ...
```

Алгоритм:

1. Регулярки якорей (case-insensitive, multiline), в порядке
   приоритета:
   - `r"(?im)^\s*БУХГАЛТЕРСКИЙ\s+БАЛАНС\s*$"` (заголовок формы).
   - `r"(?im)^\s*Бухгалтерский\s+баланс\s*$"` (он же, lower-case).
   - `r"(?i)\bФорма\s+по\s+ОКУД\s+0?710001\b"` (код Минфина).
   - `r"(?im)^\s*ОТЧЕТ\s+О\s+ФИНАНСОВЫХ\s+РЕЗУЛЬТАТАХ\s*$"` —
     запасной вариант, если баланс идёт после ОФР.
2. Найти первое вхождение по приоритету. Запомнить его смещение и
   человеко-читаемую метку (`"БУХГАЛТЕРСКИЙ БАЛАНС"`,
   `"ОКУД 0710001"`, …).
3. Если не нашли ни одного якоря — вернуть
   `BalanceTrimResult(content=None,
                      anchor_label_seen=None,
                      warnings=("balance_anchor_not_found",))`.
4. Если нашли — взять `text[match_start:]`, обрезать до `max_chars`
   (с предупреждением `"balance_trim_capped"` если резали).
5. Префиксом приклеить короткий хедер для LLM (~150 символов):
   `"Перед тобой формы РСБУ-отчётности (баланс, ОФР, отчёт об
   изменениях капитала). Извлекай числа только из этих форм.
   Аудиторские пояснения, если они встретятся ниже, для KPI не
   используй."`. Это и есть `content`.
6. Возвращать `BalanceTrimResult(content=<header>+<text>,
                                  anchor_label_seen="...",
                                  warnings=())`.

`max_chars` — отдельный knob от Text Extractor'овского `max_chars` (на
весь документ); 200 000 покрывает реальные кейсы (баланс + ОФР +
пояснительная записка ≈ 50–100k chars даже на крупных эмитентах).

### 2. `src/edx/stages/metric_extractor/service.py`

В `_assemble_user_text` (текущие строки ~371–443) после блока для
`standard == "ISSUER"` добавить аналогичный блок для `RSBU`:

```python
if standard == "RSBU":
    trim = extract_balance_sheet_onwards(
        doc_text, max_chars=self.balance_trim_max_chars
    )
    if trim.content is not None:
        self._log.info(
            "metric_extract_balance_anchor_trimmed",
            publication_id=pub.publication_id,
            document_id=doc.document_id,
            anchor_label=trim.anchor_label_seen,
            chars_after_trim=len(trim.content),
            chars_before=len(doc_text),
        )
        doc_text = trim.content
    else:
        for warning in trim.warnings:
            self._log.warning(
                "metric_extract_balance_anchor_missing",
                publication_id=pub.publication_id,
                document_id=doc.document_id,
                detail=warning,
            )
        # Fallback — отдаём полный текст: лучше шумный context, чем нулевой.
```

Импорт:
`from edx.stages.text_extractor.balance_anchor import extract_balance_sheet_onwards`.

В `__init__` добавить `balance_trim_max_chars: int = 200_000`.

Принципиально — обрезка идёт **per-document** (внутри цикла
`for doc in chosen`), а не на склеенном `user_text`. Это важно: если
документов несколько (редкий случай — отчёт + аудиторское заключение
отдельным файлом), каждый обрезается независимо.

### 3. `src/edx/config/app_config.py`

В `MetricExtractorConfig` добавить:

```python
balance_trim_max_chars: int = Field(default=200_000, gt=0)
```

### 4. `config/app.yaml`

Под `metric_extractor:` добавить:

```yaml
# Patch 30: при текст-путь для RSBU-источника обрезаем всё до якоря
# «БУХГАЛТЕРСКИЙ БАЛАНС» / «Форма по ОКУД 0710001». Это срезает 20-30k
# символов аудиторского preamble (Кэпт/Б1/ДРТ) и оставляет только
# формы. Поднимать порог нужно только если LLM начнёт жаловаться, что
# балансовая форма + ОФР + пояснения суммарно длиннее.
balance_trim_max_chars: 200000
```

### 5. Тесты

Создать `tests/stages/text_extractor/test_balance_anchor.py`:

- `test_anchor_uppercase_balance_label_found`: вход = `"...
  preamble ...\nБУХГАЛТЕРСКИЙ БАЛАНС\n... rows ...\n"`, ожидаем
  `content` начинается с хедера про РСБУ-формы и содержит баланс.
  `anchor_label_seen == "БУХГАЛТЕРСКИЙ БАЛАНС"`.
- `test_anchor_capitalised_label_found`: «Бухгалтерский баланс» —
  тоже находит.
- `test_anchor_okud_form_code_found`: фраза `Форма по ОКУД 0710001`
  достаточна (анкор балансовой формы, даже если заголовок не
  набран отдельной строкой).
- `test_anchor_okud_form_code_with_spaces`: `Форма  по\tОКУД\n0710001` тоже находит.
- `test_falls_back_to_pl_anchor`: если в тексте нет «БУХГАЛТЕРСКИЙ
  БАЛАНС», но есть `ОТЧЕТ О ФИНАНСОВЫХ РЕЗУЛЬТАТАХ` — берём его.
- `test_no_anchor_returns_none`: чисто аудиторский текст без форм
  → `content is None`, `warnings = ("balance_anchor_not_found",)`.
- `test_max_chars_truncates_with_warning`: длинный input (300 000
  chars after the anchor) → `content` ≤ `max_chars + len(header)`,
  warning `balance_trim_capped`.
- `test_first_priority_anchor_wins_over_later`: input содержит и
  «Форма по ОКУД 0710001», и «БУХГАЛТЕРСКИЙ БАЛАНС» (но в обратном
  порядке) — берём то, что в порядке приоритета первым по тексту:
  идти позиционно вперёд или приоритизировать заголовок-форму.
  В реализации — анкор с наименьшим `match.start()` среди всех
  совпадающих регулярок.
- `test_prefix_header_included`: `content.startswith("Перед тобой
  формы РСБУ-отчётности")`.

Расширить `tests/stages/metric_extractor/test_service.py` или новый
`test_balance_anchor_routing.py`:

- `test_rsbu_text_path_invokes_balance_trim`: фейковый
  `text_extract_path` JSON содержит «preamble Кэпт ...
  БУХГАЛТЕРСКИЙ БАЛАНС … rows», `standard="RSBU"`. Проверить через
  `caplog`, что событие `metric_extract_balance_anchor_trimmed`
  выпущено и `chars_after_trim < chars_before`.
- `test_ifrs_text_path_does_not_invoke_balance_trim`: тот же
  входной текст, но `standard="IFRS"` — события не выпускаются,
  user_text содержит preamble.
- `test_rsbu_no_anchor_falls_back_to_full_text`: текст без якоря →
  warning `metric_extract_balance_anchor_missing`, и user_text
  включает все pages.

### 6. `PIPELINE_LOGIC.md`

В §5.4 («Что попадает в user_text / PDF») после блока про ISSUER 1.4
добавить аналогичный блок для RSBU + якорь баланса. В §11 добавить
строку: «слишком много шума в LLM-ответе для RSBU → проверить
`metric_extract_balance_anchor_trimmed` в логе; если стоит
`balance_anchor_missing` — сначала разобраться, почему якорь не
найден (битый OCR, нестандартный formatting), потом усиливать regex».

## Тесты, которые должны проходить

- Все 9 + 3 = 12 новых тестов выше зелёные.
- Существующие тесты Metric Extractor (Patch 21 ISSUER trim в т.ч.) не
  сломаны.
- `make lint typecheck test` зелёный.

## Definition of Done

- На синтетической фикстуре «Кэпт preamble (15k chars) +
  БУХГАЛТЕРСКИЙ БАЛАНС + цифры» Metric Extractor шлёт в LLM только
  части после якоря (плюс короткий header).
- На реальном `CHMF-3-1913112` (после Patch 29 ушёл в text-path),
  `metric_extract_balance_anchor_trimmed` логирует `chars_before ≈
  86615, chars_after ≈ 50000` (точные числа — по факту, главное —
  что обрезаем не меньше 30k).
- IFRS-документы маршрут не меняется (нет события
  `metric_extract_balance_anchor_*`).
- В случае «якорь не найден» пайплайн НЕ падает — отправляет полный
  текст и пишет warning. Это критическое требование к fail-soft
  поведению.
- `PIPELINE_LOGIC.md` обновлён.
