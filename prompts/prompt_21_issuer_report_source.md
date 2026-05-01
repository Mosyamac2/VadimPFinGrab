# Промпт 21. Отчёт эмитента (type=5) как третий источник метрик

## Цель
Подключить ежеквартальный «Отчёт эмитента» (`/portal/files.aspx?id=X&type=5`) как третий по приоритету источник метрик после МСФО и РСБУ. Внутри отчёта эмитента раздел **«1.4 Основные финансовые показатели»** содержит готовые KPI-таблицы — это идеальный сжатый источник, особенно для случаев, когда ни МСФО, ни РСБУ за период недоступны (типичный кейс — небольшие эмитенты).

## Контекст
- `PLAN_e-disclosure_parser_v2.md` раздел 6, Patch 21.
- ТЗ §5.1 и §5.4.
- **Реальный пример**, на котором проверяем: `new_info/Сбер_ОЭ_6м2025_ПредпЦБ.zip` → 62-страничный PDF; раздел 1.4 распознан на страницах ~10–13 (1.4.1, 1.4.2, 1.4.3, 1.4.4 «Иные финансовые показатели»).
- Зависит от Patch 16 (Discoverer уже забирает type=5 и пишет `report_type_code=5`, `reporting_standard='ISSUER'`) и Patch 17 (БД понимает `reporting_period_*`).

## Задачи

### 1. Reporting standard в Classifier
В `src/edx/stages/classifier/heuristics.py`:
- Добавить набор маркеров для ISSUER: `"Отчёт эмитента"`, `"Ежеквартальный отчёт эмитента"`, нумерация разделов `"1.4. Основные финансовые показатели"` (с точкой и без), упоминания «эмитент эмиссионных ценных бумаг».
- Если эвристика возвращает `ISSUER`, но `report_type_code != 5` (или наоборот) — warning, но **первоисточник** — `report_type_code` из Discoverer (Patch 16). Эвристика остаётся как «второе мнение» для отчётности 2018–2020 годов, когда type-кодов не было.

### 2. Trim PDF до раздела 1.4
В `src/edx/stages/text_extractor/issuer_trim.py` (новый модуль):
- Функция `extract_section_1_4(text: str, *, max_chars: int) -> SectionExtract`:
  ```python
  @dataclass(frozen=True)
  class SectionExtract:
      content: str | None              # обрезанный фрагмент
      anchor_label_seen: str | None    # какая именно формулировка заголовка сработала
      end_anchor_seen: str | None
      warnings: list[str]
  ```
- **Якорь начала** — regex с alternation, чтобы покрыть три известные формулировки заголовка раздела 1.4 (старая редакция Положения 454-П, действующая, и сокращённый вариант, который встречается у небольших эмитентов):
  ```python
  ANCHOR_START = re.compile(
      r"""(?xm)
      ^\s*1\s*\.\s*4\s*[.\s]+
      (?P<label>
          Основные\s+финансовые\s+показатели            |
          Основные\s+финансово[\s\-—]?экономические\s+показатели  |
          Основные\s+показатели\s+финансово[\s\-—]?хозяйственной\s+деятельности
      )
      """,
  )
  ```
  Все вариации с `\s+` учитывают NBSP / тонкий пробел / разрыв строки. Дефис допускается обычный, en-dash и em-dash (см. `[\s\-—]?`). Тестируется на ≥1 настоящем PDF (Sber `Issuer Report`); при появлении новой реальной фикстуры от не-Сбера регекс расширяется.
- **Якорь конца** — `(?m)^\s*1\s*\.\s*5\s*[.\s]` ИЛИ `(?m)^\s*2\s*\.\s+\S` — что встретится раньше после старта.
- Если найден старт, но не конец — взять `min(start + max_chars, len(text))` и записать warning «end-anchor not found, content truncated by max_chars».
- Если не найден старт — `content=None`, warning «section 1.4 anchor not found», и в Metric Extractor уходит полный текст (graceful fallback).
- Альтернатива на уровне страниц (если pymupdf-страницы доступны): использовать `pages_classification` из Patch 18, чтобы взять страницы, на которых найден якорь начала и до якоря конца. Это **дополнение**, не замена — текстовый regex остаётся первым выбором, страницы — оптимизация для длинных Issuer Report'ов.
- Конфиг: `text_extractor.issuer_trim_max_chars: int = 30_000`.

### 3. Metric Extractor: трёхуровневый приоритет
- В `MetricExtractorService._pick_documents` использовать `profile.reporting_priority` (после Patch 19), значение `[IFRS, RSBU, ISSUER]`.
- За один и тот же `(ticker, year, period_type)` (через `PublicationsRepo.list_by_period`, добавлен в Patch 17) выбирать единственный документ согласно приоритету: сначала пытаемся IFRS, при отсутствии — RSBU, при отсутствии — ISSUER.
- В `MetricRow.source` писать соответственно `"IFRS"`, `"RSBU"`, `"ISSUER"`. В QA-отчёт `qa_report` строки с `source="ISSUER"` НЕ помечаются `is_incomplete` только из-за источника — критерий полноты тот же (5 метрик профиля).

### 4. LLM-промпт под ISSUER
- В `MetricExtractorService` при формировании промпта, если документ ISSUER, передавать только обрезанный фрагмент раздела 1.4. В системную часть промпта добавить пояснение: «Это раздел 1.4 ежеквартального отчёта эмитента; ищи именно сводные KPI, не таблицы с детализацией по сегментам».
- Schema для `tool_use`/JSON-ответа не меняется.

### 5. Тесты
**Реальная фикстура (Сбер):**
- `test_extract_section_1_4_real_sber_issuer`: распакованный PDF из `new_info/Сбер_ОЭ_6м2025_ПредпЦБ.zip` → `content` непустой, содержит `1.4`, не содержит `1.5` / `2.`.

**Синтетические тесты на регекс с alternation** (на случай, когда у формулировки заголовка нет реальной фикстуры — нужна страховка):
- `test_extract_section_1_4_label_finansovye`: текст с заголовком «1.4 Основные финансовые показатели» → найден.
- `test_extract_section_1_4_label_finansovo_economicheskie`: «1.4. Основные финансово-экономические показатели» → найден, `anchor_label_seen` различается.
- `test_extract_section_1_4_label_finansovo_hozyajstvennoj`: «1.4 Основные показатели финансово-хозяйственной деятельности» → найден.
- `test_extract_section_1_4_handles_unicode_spaces`: nbsp / тонкий пробел между «1.4» и «Основные» → найден.
- `test_extract_section_1_4_handles_em_dash_in_label`: «финансово—экономические» (em-dash) → найден.
- `test_extract_section_1_4_no_anchor_returns_none`: на левом тексте без раздела возвращает `content=None` + warning.
- `test_extract_section_1_4_truncates_when_no_end_anchor`: старт найден, конца нет → возвращает `min(start+max_chars, end_of_text)` + warning.

**Параметризация на будущее:** тест-фабрика `parametrize_real_issuer_reports` принимает каталог `tests/fixtures/pdf/issuer/` и автоматически прогоняет `extract_section_1_4` на каждом найденном PDF. Когда у пользователя появится Issuer Report не-Сбера — он его кладёт в каталог, и параметризованный тест автоматически подхватит. Если регекс на новом отчёте промахнётся — тест упадёт и потребует расширить alternation **с приложением реального заголовка**, а не «по эрудиции».

**Источники и приоритет:**
- `test_metric_extractor_falls_back_ifrs_rsbu_issuer`: моковая БД с тремя публикациями за один период (IFRS, RSBU, ISSUER) → выбирается IFRS. С двумя (RSBU, ISSUER) → RSBU. С одним ISSUER → ISSUER.
- `test_metric_extractor_uses_trimmed_text_for_issuer`: мок LLM ловит `prompt`; для ISSUER длина промпта ≤ `issuer_trim_max_chars + boilerplate`, для IFRS — без обрезки до 1.4.
- `test_classifier_marks_issuer_from_type_code`: Classifier видит `report_type_code=5` → ставит `reporting_standard='ISSUER'` без обращения к эвристикам.

### 6. README/USER_GUIDE
- В таблицу «Какие отчёты собираются» добавить строку «Отчёт эмитента (type=5) — fallback, когда нет МСФО/РСБУ; Metric Extractor использует только раздел 1.4».

## Definition of Done
- Стадия Discoverer (после Patch 16) собирает type=5; Metric Extractor умеет с ним работать.
- На реальном `new_info/Сбер_ОЭ_6м2025_ПредпЦБ.pdf` происходит trim до раздела 1.4 и LLM получает ≤30k символов.
- При наличии IFRS за тот же период ISSUER не используется (приоритет).
- Excel-витрина: для эмитентов без МСФО/РСБУ за период появляются строки с `source="ISSUER"`.
- `make lint typecheck test` зелёные.
