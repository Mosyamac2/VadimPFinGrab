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
