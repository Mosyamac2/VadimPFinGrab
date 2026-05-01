# Промпт 18. Постраничная классификация PDF: text + scan в одном документе

## Цель
Заменить документо-уровневое решение «весь PDF — текстовый или скан» постраничной классификацией. Документы с гибридной структурой (например, банковские формы РПБУ 0409806/0409807: первые страницы — машинописный текст, далее — скан-вставки) должны давать **тексту** максимально возможный охват, а **OCR** включаться только для тех страниц, где текстового слоя нет.

## Контекст
- `PLAN_e-disclosure_parser_v2.md` раздел 3, Patch 18.
- ТЗ §6.1 (правило про OCR-fallback) и §7.1 п.4 (контракт Classifier).

**Реальные распределения, измеренные `pymupdf.get_text()` на материалах из `new_info/`:**

| PDF | Страниц | Распределение | Тип |
|---|---|---|---|
| `RPBU_9м2025.pdf` (Сбер РПБУ) | 17 | стр. 1–4 текст (167–3310 chars), стр. 5–17 — 0 chars | **гибрид** |
| `Бухгалтерская отчетность на 31.03.2026.pdf` (LKOH РСБУ Q1) | 24 | все 24 — текст (399–2402 chars) | чистый текст |
| `VTB-GO-2024_fin RUS.pdf` (ВТБ годовой) | 186 | первые 30 проверены: все текст (77–5620 chars) | чистый текст |
| `ГО2025_раскрываемый.pdf` (LKOH годовой) | 131 | первые 30 проверены: все текст (505–10162 chars) | чистый текст |

Из этих чисел вытекает:
- Текущая `is_machine_readable` (`min_text_chars=400`, `first_pages_to_inspect=3`) на Сбере РПБУ ошибочно возвращает `True` — Metric Extractor никогда не видит сканированные формы 0409806/0409807, где лежит net interest income/total assets.
- На корпоративных и небанковских отчётах OCR не нужен — Patch 18 не должен **регрессировать** на этих документах (никакого OCR-вызова, никакого замедления).
- Гибридный кейс — реальный и достаточно частый именно для **банковских РПБУ**, где первая часть — машинописные пояснения, дальше — сканированные формы регулятора.

## Задачи

### 1. Заменить контракт Classifier
В `src/edx/stages/classifier/pdf_inspector.py`:
- Удалить `is_machine_readable(path) -> bool`.
- Добавить:
  ```python
  @dataclass(frozen=True)
  class PageClassification:
      page_index: int            # 0-based
      char_count: int
      kind: Literal["text", "scan"]

  def classify_pages(path: Path, *, min_text_chars_per_page: int) -> list[PageClassification]:
      ...
  ```
- Логика: открыть PDF через pymupdf один раз, по каждой странице снять `len(page.get_text("text").strip())`, сравнить с порогом, вернуть полный список.
- В `src/edx/stages/classifier/service.py` старое поле `pdf_kind: 'machine_readable' | 'scan' | 'unknown'` оставить для обратной совместимости (агрегируем: если хотя бы одна страница `text` — `machine_readable`; иначе — `scan`), но дополнительно сохранять `pages_classification` в `documents`-таблицу как JSON.

### 2. Миграция `0008_documents_pages.sql`
```sql
ALTER TABLE documents ADD COLUMN pages_classification TEXT;  -- JSON: [{"page":0,"chars":167,"kind":"text"}, ...]
ALTER TABLE documents ADD COLUMN text_pages_count INTEGER;
ALTER TABLE documents ADD COLUMN scan_pages_count INTEGER;
```
Все nullable, индексы не нужны.

### 3. Конфиг
В `src/edx/config/app_config.py:ClassifierConfig`:
- Оставить `min_text_chars` и `first_pages_to_inspect` для обратной совместимости (помечены deprecated в комментарии).
- Добавить `min_text_chars_per_page: int = 50` (страница считается текстовой, если на ней ≥50 непустых символов; ниже — скан).
- В `config/app.yaml` пример прокинуть.

### 4. Гибридный Text Extractor
В `src/edx/stages/text_extractor/pdf.py`:
- Текущий путь чтения текста через pymupdf оставить.
- Добавить: если документ помечен как `machine_readable` И в `pages_classification` есть хотя бы одна страница `kind="scan"`, прогнать OCR **только** по этим страницам (через тот же `pdf2image+pytesseract`, что и для чисто-сканных документов), результаты слить в общий текст с маркером:
  ```
  --- page 5 (OCR) ---
  ...распознанный текст...
  ```
- Сохранить order: страницы идут в естественном порядке, чтобы LLM видела непрерывный документ.

### 5. Repositories
- `DocumentsRepo.set_classification(...)` принимает `pages_classification: list[dict] | None`, `text_pages_count: int | None`, `scan_pages_count: int | None`.
- `DocumentRow` — три новых поля.

### 6. Тесты (мульти-эмитент, обязательно)
Все три **реальных** PDF берутся из распакованных архивов в `new_info/` и кладутся в `tests/fixtures/pdf/`:
- `sber_rpbu_9m2025.pdf` ← `Сбербанк_РПБУ_9м2025.zip` (банк, гибрид)
- `lkoh_rsbu_q1_2026.pdf` ← `Buhgalterskaya otchetnost' na 31.03.2026.pdf.zip` (нефтегаз, чистый текст)
- `vtb_go_2024.pdf` ← `VTB-GO-2024_fin RUS.pdf.zip` (банк, годовой отчёт, чистый текст; обрезать через `qpdf` до первых 30 страниц для сокращения веса git)

**Классификация страниц:**
- `test_classify_pages_sber_rpbu_hybrid`: `text_pages == [0,1,2,3]`, `scan_pages == [4..16]`.
- `test_classify_pages_lkoh_rsbu_all_text`: все 24 страницы `kind="text"`, `scan_pages_count == 0`.
- `test_classify_pages_vtb_go_first30_all_text`: все проверенные страницы `kind="text"`.
- `test_classify_pages_synthetic_all_scan`: PDF из изображений → все `scan`.

**OCR-инвокация (анти-регрессионные тесты, разные эмитенты):**
- `test_extractor_ocrs_only_scan_pages_in_sber_hybrid`: мок OCR ловит вызовы; на гибриде вызывается ровно для индексов `[4..16]`.
- `test_extractor_no_ocr_on_lkoh_rsbu_all_text`: мок OCR; **counter == 0** — на чисто-текстовом РСБУ корпората OCR не вызывается ни разу.
- `test_extractor_no_ocr_on_vtb_go_all_text`: то же для ВТБ ГО — ни одного OCR-вызова.
- `test_extractor_ocrs_all_pages_on_full_scan`: на синтетическом all-scan PDF OCR вызывается на всех страницах.

**Журналы:**
- `test_classifier_writes_pages_classification_json`: после `service.run` в БД появилась JSON-колонка с правильным распределением.
- `test_log_extractor_ocr_partial_only_for_hybrid`: лог `text_extractor_ocr_partial` появляется на Сбер РПБУ и **не появляется** на LKOH РСБУ / VTB ГО.

### 7. Реальные фикстурные PDF
Распаковать архивы из `new_info/` в `tests/fixtures/pdf/`:
- `sber_rpbu_9m2025.pdf` (~1.5 MB, 17 стр.) — оставить как есть.
- `lkoh_rsbu_q1_2026.pdf` (~5 MB, 24 стр.) — при необходимости урезать `qpdf --pages in.pdf 1-24 -- in.pdf out.pdf` (он уже 24 страницы, но это страховка от лишних embedded ресурсов).
- `vtb_go_2024_first30.pdf` (~6 MB, 186 стр.) — обрезать до первых 30: `qpdf --pages in.pdf 1-30 -- in.pdf out.pdf`. Полный 186-страничный документ для тестов классификатора излишен и раздует репо.

Если суммарный размер пугает — занести `tests/fixtures/pdf/*.pdf` в git LFS либо в `.gitignore` с инструкцией «распакуйте `new_info/*.zip` перед запуском тестов» (CI читает `new_info/`). Решение фиксировать в README раздела «Запуск тестов».

## Тесты, которые должны проходить
- Новые тесты выше — все зелёные.
- Существующий `test_classifier_marks_scan` адаптирован к новому контракту (`pdf_kind` остаётся, плюс проверяется `scan_pages_count > 0`).
- `make lint typecheck test`.

## Definition of Done
- На реальном Сбер РПБУ 9М2025 пайплайн (Classifier → Text Extractor) генерирует объединённый текст из 4 текстовых страниц + 13 OCR-страниц; `text_pages_count=4`, `scan_pages_count=13`.
- На реальном LKOH РСБУ Q1 2026 классификатор отдаёт `scan_pages_count=0`; OCR не вызывается ни разу — это проверяется мок-каунтером.
- На реальном VTB ГО 2024 (или его первых 30 страницах) то же — `scan_pages_count=0`, OCR не вызывается.
- На синтетическом all-scan документе OCR вызывается на всех страницах.
- Лог `text_extractor_ocr_partial` появляется ровно тогда, когда вызывается частичный OCR (т.е. на гибриде), и не появляется на all-text/all-scan документах.
