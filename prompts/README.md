# Серия промптов для реализации e-disclosure extractor

Источник требований: `TZ_e-disclosure_extractor.md` в корне репозитория.

Промпты исполняются **строго последовательно** — каждый следующий опирается на артефакты предыдущего. После каждого промпта обязательно прогнать все указанные в нём тесты и убедиться, что они зелёные, прежде чем переходить дальше.

## Порядок выполнения

| # | Промпт | Что появляется в репозитории |
|---|---|---|
| 01 | [Каркас проекта](prompt_01_scaffolding.md) | структура каталогов, `pyproject.toml`, базовый CLI, structlog, pytest |
| 02 | [Конфигурация и секреты](prompt_02_configuration.md) | `config/*.yaml`, Pydantic-схемы, загрузка `.env` |
| 03 | [State-БД (SQLite)](prompt_03_state_db.md) | схема SQLite + миграции + репозитории |
| 04 | [HTTP-клиент и Discoverer](prompt_04_http_discoverer.md) | rate-limited httpx, парсинг карточек эмитентов, поиск новых публикаций |
| 05 | [Downloader и Unpacker](prompt_05_downloader_unpacker.md) | скачивание архивов с дедупликацией, распаковка RAR/ZIP |
| 06 | [PDF Classifier](prompt_06_pdf_classifier.md) | определение типа отчётности, признака скана, типа формы |
| 07 | [Text Extractor (нативный + OCR)](prompt_07_text_extractor.md) | pdfplumber/pymupdf + Tesseract OCR |
| 08 | [LLM-провайдер с fallback](prompt_08_llm_provider.md) | абстракция, Anthropic API (приоритет) + OpenRouter (fallback) |
| 09 | [Metric Extractor](prompt_09_metric_extractor.md) | извлечение финансовых показателей через LLM со строгой JSON-схемой |
| 10 | [Event Extractor](prompt_10_event_extractor.md) | структурирование сообщений о существенных фактах |
| 11 | [Validator (sanity checks)](prompt_11_validator.md) | балансовое уравнение, знаки, YoY, валюты, единицы |
| 12 | [Writer: SQLite-витрина + Excel](prompt_12_writer_excel.md) | запись в витрину `state.sqlite`, генерация `e-disclosure.xlsx` |
| 13 | [Репликация на Google Drive](prompt_13_google_drive.md) | OAuth2, update (а не create), опциональные снапшоты |
| 14 | [Оркестратор и CLI](prompt_14_orchestrator_cli.md) | DAG стадий, `update` / `run --full-reload`, идемпотентность |
| 15 | [Расписание и приёмочные тесты](prompt_15_scheduling_acceptance.md) | crontab/systemd timer, e2e-тесты, README с инструкцией по установке |

## Серия Patch 16–22 (адаптация под реальный e-disclosure.ru) — ✅ закрыта

После выкатки v1 на реальный сайт обнаружилось, что синтетические фикстуры, на которых написаны промпты 04, 06, 09, не совпадают с настоящей разметкой/структурой документов. План доработок собран в `PLAN_e-disclosure_parser_v2.md`; вся серия 16–22 реализована и помержена в master (см. историю коммитов с префиксами `feat(discoverer)`, `feat(classifier+extractor)`, `feat(metrics)`, `feat(tickers)`, `feat(metric-extractor)`, `docs(...)`).

**Принцип «мульти-эмитент»** проходит через всю серию: каждая поведенческая стадия (Discoverer/Classifier/Metric Extractor/Issuer-trim) тестируется на материалах **нескольких эмитентов из разных секторов**. На текущий момент задействованы:
- SBER (`id=3043`) — банк: 4 HTML-листинга, МСФО, РПБУ-гибрид, Issuer Report
- LKOH (`id=17`) — нефтегаз: HTML-листинг РСБУ (с 2009 г.), РСБУ Q1 2026 (чисто текст), Годовой отчёт 2025 (чисто текст)
- VTBR — банк не-Сбер: Годовой отчёт 2024 (чисто текст; уникальные термины «Чистые процентные доходы» во мн. ч.)

Любой код, неявно завязанный на разметку или термины одного конкретного эмитента, считается багом. Любой добавляемый синоним метрики обязан иметь подтверждение реальной фикстурой (см. `prompt_19`).

| # | Промпт | Что приземлилось | Breaking? |
|---|---|---|---|
| 16 | [Discoverer на реальном HTML](prompt_16_discoverer_real_html.md) | парсер `table.files-table`, обход 4 URL `files.aspx?id=X&type={2,3,4,5}` на тикер, fail-soft на отсутствующие типы; новый модуль `discoverer/period.py` | да: контракт `DiscoveredPublication` расширен (4 новых поля) |
| 17 | [Колонки период/тип в БД](prompt_17_publications_period_columns.md) | миграция `0007_publications_period.sql`: `report_type_code`, `reporting_period_year`, `reporting_period_type` + индекс; `PublicationsRepo.list_by_period` | нет (поля nullable) |
| 18 | [Постраничная классификация PDF](prompt_18_per_page_classification.md) | миграция `0008_documents_pages.sql` + `classify_pages()`; гибридный Text Extractor (нативный текст + OCR только для scan-страниц); `text_extractor_ocr_partial` лог только на гибридах | да: `is_machine_readable(...)` → `classify_pages(...)`, `app.classifier.min_text_chars_per_page` |
| 19 | [Профили банк / небанк](prompt_19_bank_nonbank_profiles.md) | `metrics.yaml → profiles: {bank, non_bank}`; `MetricSpec` с `synonyms`/`only_in_sources`/`aggregation_hint`; `TickerEntry.profile`; per-(profile, source) prompt+schema; Excel лист `tickers` с колонкой `profile`; удалён мёртвый `formula.py` | **да**: старый плоский `metrics.yaml` больше не загружается |
| 20 | [Top-50 tickers.yaml](prompt_20_top50_tickers.md) | scaffold `config/tickers.yaml` (51 эмитент, SBER+LKOH с реальными id); CLI `tools/find_e_disclosure_ids.py` (поиск id) и `tools/validate_tickers.py` (probe `OK/EMPTY/MISSING/ERROR` per type); `config/tickers.yaml.template` | нет |
| 21 | [Issuer Report как 3-й источник](prompt_21_issuer_report_source.md) | миграция `0009_issuer_reporting_standard.sql` (CHECK widening); `text_extractor/issuer_trim.py` (regex с alternation для трёх формулировок 1.4 + TOC-эвристика); 3-tier приоритет [IFRS, RSBU, ISSUER] в Metric Extractor; ISSUER-источник не отправляется как PDF, текст обрезается до 1.4; удалён ISSUER→RSBU shim из Patch 19 | нет |
| 22 | [Документация и косметика](prompt_22_cosmetic_docs.md) | README/USER_GUIDE под профили + sources tickers.yaml workflow; `app.yaml` с `min_text_chars_per_page` + `issuer_trim_max_chars`; `metrics.yaml.template`; `unrar` → опциональный | нет |

**Фактический порядок исполнения** (с метками коммитов): `17 + 16` (один коммит — миграция нужна до парсера) → `18` → `19` → `20` → `21` → `22`. Patch 20 пришёлся между поведенческими патчами, потому что инструменты валидации потребовались для проверки Discoverer на боевой выборке.

**Зависимости от внешнего сайта:** Patch 16 предполагает, что у пайплайна уже есть рабочий обход анти-бота ServicePipe (UA + cookies или headless-Chromium). Если на момент исполнения Patch 16 анти-бот всё ещё блокирует прямой `httpx`, фикстуры остаются единственным способом проверить парсер; интеграционная проверка откладывается.

## Соглашения

- Каждый промпт приводит ссылку на разделы ТЗ, к которым он относится.
- В каждом промпте есть блок **«Тесты, которые должны проходить»** — это gating-критерий перехода к следующему этапу.
- Не объединять промпты и не идти вперёд при незелёных тестах.
- Рабочее дерево после каждого промпта должно оставаться запускаемым (`pytest -q` зелёный).

## Параметры запуска промптов

Перед стартом каждого промпта:

```
git checkout -b step-NN-<short-name>
```

После прохождения тестов:

```
git add -A && git commit -m "step NN: <short summary>"
```

Это даст возможность откатиться к любому этапу без потери работы.
