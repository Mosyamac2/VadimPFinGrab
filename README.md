# e-disclosure extractor

ETL-пайплайн, который раз в сутки забирает финансовую отчётность Top-50
эмитентов Московской биржи с портала
[e-disclosure.ru](https://www.e-disclosure.ru/), извлекает ключевые
показатели через LLM, валидирует их и публикует Excel-витрину на Google
Drive вместе со сквозным потоком сообщений о существенных фактах.

Полное техническое задание: [`TZ_e-disclosure_extractor.md`](TZ_e-disclosure_extractor.md).
Серия implementation-промптов: [`prompts/README.md`](prompts/README.md).
План доработок под боевой сайт: [`PLAN_e-disclosure_parser_v2.md`](PLAN_e-disclosure_parser_v2.md).

> **Текущий статус (на 2026-05-02).** v1 (этапы 01–15) и серия Patch 16–22
> (адаптация под боевой e-disclosure.ru) реализованы и помержены. Discoverer
> читает реальный `table.files-table` и обходит 4 URL `files.aspx?id=X&type=Y`
> на тикер; Classifier классифицирует страницы text/scan по отдельности, и
> банковские РПБУ-гибриды (текст + сканы) идут через частичный OCR без потерь;
> Metric Extractor использует профили `bank` / `non_bank` из
> `config/metrics.yaml` и трёхуровневый приоритет источников
> `IFRS → RSBU → ISSUER` (Issuer Report — type=5, обрезается до раздела 1.4
> «Основные финансовые показатели» перед LLM). Перед первым `edx update` на
> новой инсталляции остаётся однократная задача оператора: заполнить
> `e_disclosure_id` в `config/tickers.yaml` (см. [§4.2](#42-сборка-configtickersyaml)).
> Подробности — в разделе [«Статус реализации»](#статус-реализации).

---

## 1. Кому это нужно

Аналитику или CFO-офису, которому раз в сутки требуется одна Excel-таблица
со сравнимыми показателями десятков эмитентов и хронологией существенных
фактов. Запуск локальный, на одной Linux-машине, без серверной
инфраструктуры — данные ходят только между e-disclosure → диск → Anthropic
(через прямой API или OpenRouter) → Google Drive.

## 2. Системные требования

| Что | Версия | Примечание |
|---|---|---|
| Python | ≥ 3.11 | целевая платформа Linux |
| Обязательные пакеты | `tesseract-ocr`, `tesseract-ocr-rus`, `tesseract-ocr-eng`, `poppler-utils` | OCR для сканированных PDF |
| Опциональные пакеты | `unrar` | страховка для устаревших RAR-публикаций; e-disclosure давно отдаёт только ZIP, по умолчанию не нужен |
| Anthropic API ключ | один из двух | прямой API даёт нативный PDF-input |
| OpenRouter API ключ | один из двух | fallback при недоступности Anthropic |
| Google аккаунт | OAuth Desktop client | для репликации Excel на Drive |

```bash
sudo apt install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils
```

Без Tesseract / poppler сканированные PDF не OCR-ятся, машинно-читаемые —
работают. `unrar` ставьте только если на портале появятся старые RAR-архивы;
ZIP-публикации (всё что встречается сейчас) проходят без него.

## 3. Установка

```bash
git clone https://github.com/Mosyamac2/VadimPFinGrab.git edx
cd edx
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
sudo apt install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils

cp .env.example .env
$EDITOR .env                # положить ключи API

edx config check            # валидирует все YAML и печатает значения с маскированием секретов
edx auth google-drive       # один раз; вставить refresh_token в .env как GOOGLE_OAUTH_REFRESH_TOKEN
```

После `edx auth google-drive` в `.env` должны лежать
`ANTHROPIC_API_KEY` (или `OPENROUTER_API_KEY`),
`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
`GOOGLE_OAUTH_REFRESH_TOKEN`.

## 4. Конфигурация

Все настройки в YAML под `config/`, валидируются Pydantic при старте.
`.env` (вне Git) — только секреты.

| Файл | Назначение |
|---|---|
| [`config/app.yaml`](config/app.yaml) | пути, расписание, knobs всех стадий (Discoverer, Downloader, Unpacker, Classifier, Text Extractor, Validator, Orchestrator, Google Drive) |
| [`config/tickers.yaml`](config/tickers.yaml) | тикер MOEX → e-disclosure id + `profile: bank \| non_bank` (см. ниже), ИНН/ОГРН, опциональный `priority_override` |
| [`config/metrics.yaml`](config/metrics.yaml) | два профиля метрик (`profiles: bank`, `profiles: non_bank`), синонимы с привязкой к реальным фикстурам, `only_in_sources` / `aggregation_hint` |
| [`config/event_types.yaml`](config/event_types.yaml) | справочник кодов событий (обязательно содержит `other`) |
| [`config/llm.yaml`](config/llm.yaml) | Claude Sonnet 4.6 как primary, OpenRouter как fallback, кеширование |
| [`config/ocr.yaml`](config/ocr.yaml) | Tesseract `rus+eng` по умолчанию; cloud OCR — заглушки |

Любая ошибка валидации YAML → exit code 2 с структурированным логом
`config_load_failed` (путь к файлу + поле).

### 4.1 Профили эмитентов (`bank` vs `non_bank`)

Для банков и корпоратов набор «правильных» KPI почти не пересекается, и в
v2 пайплайн извлекает их разными списками. Профиль выбирается по полю
`profile` каждой записи в [`config/tickers.yaml`](config/tickers.yaml).

| Профиль | Метрики (из [`config/metrics.yaml`](config/metrics.yaml)) | Кто туда попадает |
|---|---|---|
| `bank` | `net_interest_income`, `net_fee_income`, `net_income`, `total_assets`, `total_equity` | SBER, VTBR, BSPB, TCSG, MBNK, SVCB |
| `non_bank` | `revenue`, `ebitda`, `net_income`, `total_assets`, `total_debt` | всё остальное в Топ-50 (нефтегаз, металлы, ритейл, телеком, …) |

`MetricSpec.only_in_sources` дополнительно вычёркивает метрики, которых
в выбранном источнике быть не может (классический пример — EBITDA в
РСБУ-документе): они не запрашиваются у LLM и не учитываются в
`completeness`. `aggregation_hint` подмешивается в RSBU-промпт там, где
показатель собирается из нескольких строк баланса (напр., `total_debt`
= строки 1410 + 1510).

### 4.2 Сборка `config/tickers.yaml`

В репозитории идёт scaffold на 51 эмитента из MOEX Top-50 с заполненными
SBER (`id=3043`) и LKOH (`id=17`) и `REPLACE_ME` для остальных
(см. `config/tickers.yaml.template` для минимальной формы). Чтобы
добавить или починить запись:

```bash
# 1. Подсказать кандидатные id из e-disclosure search:
python tools/find_e_disclosure_ids.py --tickers VTBR,GAZP,ROSN

# 2. Вставить выбранные id руками в config/tickers.yaml — скрипт сам
#    не редактирует файл (id одной группы могут отличаться у головной
#    компании и дочки, выбор за оператором).

# 3. Проверить, что для каждого тикера хотя бы один из type=3/4/5
#    отдаёт реальные публикации:
python tools/validate_tickers.py --strict
```

`validate_tickers.py` различает `OK / EMPTY / MISSING / ERROR` для
каждой пары `(тикер, type)`. `MISSING` для одного типа — не баг
(канонический пример: LKOH `id=17` не публикует МСФО под этим id —
для Лукойла работаем только с РСБУ). С `--strict` exit-code 1 встанет
только когда у тикера ни один из `type=3/4/5` не дал OK.

### 4.3 Источники метрик (приоритет в `metrics.yaml → reporting_priority`)

| Источник | Где живёт на e-disclosure | Когда используется |
|---|---|---|
| **IFRS** (МСФО) | `files.aspx?id=X&type=4` | первый приоритет; полный набор показателей профиля |
| **RSBU** (РСБУ) | `files.aspx?id=X&type=3` | второй приоритет; для `only_in_sources=[IFRS,…]` метрик не запрашивается у LLM |
| **ISSUER** (Отчёт эмитента) | `files.aspx?id=X&type=5` | fallback третьего уровня; перед LLM текст автоматически режется до раздела 1.4 «Основные финансовые показатели» (~14k символов вместо 60+ страниц) |
| `ANNUAL` (Годовой отчёт type=2) | `files.aspx?id=X&type=2` | metadata-only, не используется как источник метрик |

## 5. Запуск

```bash
edx update                          # инкрементальный прогон (кнопка «обновить»)
edx run --full-reload               # полная переобработка последних 3 лет
edx run --ticker SBER --ticker GAZP # прогон только по выбранным тикерам
edx status                          # последние 5 запусков с агрегатами
```

Все стадии можно запустить изолированно для отладки:

```bash
edx discover [--ticker SBER]
edx download [--publication-id ID]
edx unpack [--publication-id ID]
edx classify [--publication-id ID]
edx extract-text [--publication-id ID]
edx extract-metrics [--publication-id ID]
edx extract-events [--publication-id ID]
edx validate [--publication-id ID]
edx export-excel
edx replicate
edx cache prune --older-than 30d
```

## 6. Расписание

Шаблоны лежат в `deploy/`. Время `04:00` соответствует
`config/app.yaml → schedule.cron_time`.

### cron

```bash
crontab -u <user> /opt/edx/deploy/cron/edx.crontab
```

### systemd timer

```bash
sudo cp deploy/systemd/edx-update.service /etc/systemd/system/
sudo cp deploy/systemd/edx-update.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now edx-update.timer
```

## 7. Где смотреть результат

- `output/e-disclosure.xlsx` — Excel-витрина (4 листа: `metrics`, `events`,
  `meta`, `qa_issues`).
- Google Drive — тот же файл с тем же `file_id` и ссылкой между запусками
  (см. `runs.excel_drive_link`). `edx status` печатает ссылку.
- Опциональные снапшоты с датой в подпапке `archive/` — флаг
  `google_drive.archive: true` в `app.yaml`.

## 8. Логи и отладка

- `logs/pipeline.log` — JSON-лог через `structlog`, ротация 10 МБ × 5
  файлов; уровень переключается переменной окружения `EDX_LOG_LEVEL`.
- `data/state.sqlite` — открывается любым SQLite-вьювером (DBeaver,
  `sqlite3`, `litecli`). Таблицы: `tickers`, `publications`, `documents`,
  `metrics`, `events`, `runs`, `qa_issues`, `schema_migrations`.
- `edx status [--limit N]` — табличный обзор последних запусков.
- LLM-кеш: `data/processed/_llm_cache/{sha256}.json`. Чистится через
  `edx cache prune --older-than 30d`.

## 9. Расширение

| Хочу добавить | Где | Перезапуск кода |
|---|---|---|
| Эмитента | `config/tickers.yaml` (`ticker`, `e_disclosure_id`, `name`) | нет |
| Финансовый показатель | `config/metrics.yaml` (`canonical_name`, синонимы, формула) | нет |
| Тип существенного факта | `config/event_types.yaml` (`code`, `display_name`, `aliases`) | нет |
| Сменить стандарт-приоритет | `config/metrics.yaml → reporting_priority` | нет |
| Подключить cloud-OCR | `config/ocr.yaml → engine: yandex_vision`, плюс заполнить ключи | да (нужно реализовать заглушку) |

## 10. Что НЕ входит в scope первой версии

Согласовано с заказчиком (раздел 18 ТЗ):

- Telegram / email уведомления.
- Мобильное приложение iOS.
- REST API.
- Динамическое подтягивание состава индекса MOEX.
- Извлечение нефинансовых показателей (ESG, операционные метрики).
- Дашборды и визуализация — только Excel-витрина.
- Алёрты по триггерам (например, «EBITDA −30% YoY»).
- Хранение исторических версий извлечений (только последняя успешная по
  публикации).

## 11. Перспективы

Архитектурно зафиксировано (раздел 15 ТЗ):

- Бизнес-логика отделена от способа запуска — над ядром можно поднять
  FastAPI без рефакторинга.
- State + витрина — в стандартных форматах (SQLite, Excel), читаются
  мобильными клиентами сторонними средствами уже сейчас.
- Нативное iOS-приложение — отдельным ТЗ позже.

---

## Разработка

```bash
make install         # установить пакет + dev зависимости
make lint            # ruff
make typecheck       # mypy strict
make test            # pytest (юнит + e2e)
pytest tests/e2e -q  # отдельно — приёмочные сценарии
```

Стек:
- `httpx` async + `aiolimiter` для polite scraping
- `selectolax` для HTML, `pymupdf` + `pdfplumber` для PDF, `pytesseract` +
  `pdf2image` для OCR
- `anthropic` (приоритет) + OpenRouter HTTP (fallback) с `tenacity`-ретраями
  и `json-repair` для устойчивого парсинга
- `openpyxl` для Excel-витрины, `google-api-python-client` для Drive
- `pydantic` 2 + `pydantic-settings` для конфигов и секретов
- `structlog` для JSON-логов, чистая `sqlite3` для state-БД, `pytest`
  + `pytest-asyncio` для тестов

Архитектура и порядок реализации описаны в [`prompts/README.md`](prompts/README.md).

## Статус реализации

### v1 — каркас и архитектура (этапы 01–15) — ✅ завершено

ТЗ закрыто архитектурно: все стадии реализованы, end-to-end тесты зелёные на
синтетических фикстурах.

| # | Этап |
|---|---|
| 01 | каркас проекта |
| 02 | конфигурация и секреты |
| 03 | SQLite state-БД + репозитории |
| 04 | HTTP-клиент + Discoverer |
| 05 | Downloader + Unpacker |
| 06 | PDF Classifier |
| 07 | Text Extractor (native + OCR) |
| 08 | LLM-провайдер (Anthropic + OpenRouter fallback) |
| 09 | Metric Extractor |
| 10 | Event Extractor |
| 11 | Validator (sanity checks + qa_issues) |
| 12 | Writer (SQLite mart + Excel) |
| 13 | Google Drive репликация |
| 14 | Оркестратор + единый CLI |
| 15 | Расписание, документация, e2e-тесты |

### v2 — адаптация под боевой e-disclosure.ru (Patch 16–22) — ✅ завершено

Пилотный запуск на реальный сайт показал, что синтетические фикстуры v1
не совпадали с фактической разметкой `e-disclosure.ru`
(`table.files-table` вместо `section.publications-section`), а реальные
документы требуют другой обработки (банковские РПБУ — гибрид текст+скан,
термины KPI у банков и корпоратов разные, у части эмитентов отсутствуют
отдельные типы публикаций). План v2 собран в
[`PLAN_e-disclosure_parser_v2.md`](PLAN_e-disclosure_parser_v2.md); вся
серия 16–22 помержена в master.

| # | Патч | Что приземлилось | Breaking? |
|---|---|---|---|
| 16 | [Discoverer на реальном HTML](prompts/prompt_16_discoverer_real_html.md) | парсер `table.files-table`, обход 4 URL `files.aspx?id=X&type={2,3,4,5}` на тикер; новый модуль `discoverer/period.py`; fail-soft на отсутствующие типы | да: контракт `DiscoveredPublication` расширен |
| 17 | [Колонки период/тип в БД](prompts/prompt_17_publications_period_columns.md) | миграция `0007_publications_period.sql` + `PublicationsRepo.list_by_period` | нет |
| 18 | [Постраничная классификация PDF](prompts/prompt_18_per_page_classification.md) | миграция `0008_documents_pages.sql`; `classify_pages()`; гибридный Text Extractor (нативный текст + OCR только для scan-страниц) | да: `is_machine_readable` → `classify_pages` |
| 19 | [Профили банк / небанк](prompts/prompt_19_bank_nonbank_profiles.md) | `metrics.yaml → profiles: {bank, non_bank}`; per-(profile, source) prompt+schema; Excel лист `tickers` с колонкой `profile` | **да**: старый плоский `metrics.yaml` больше не загружается |
| 20 | [Top-50 tickers.yaml](prompts/prompt_20_top50_tickers.md) | scaffold на 51 эмитента (SBER+LKOH с реальными id); CLI `tools/find_e_disclosure_ids.py` + `tools/validate_tickers.py`; `tickers.yaml.template` | нет |
| 21 | [Issuer Report как 3-й источник](prompts/prompt_21_issuer_report_source.md) | миграция `0009_issuer_reporting_standard.sql`; `text_extractor/issuer_trim.py` (regex 1.4 + TOC-эвристика); 3-tier приоритет `[IFRS, RSBU, ISSUER]` в Metric Extractor | нет |
| 22 | [Документация и косметика](prompts/prompt_22_cosmetic_docs.md) | README/USER_GUIDE/templates под профили, ISSUER, tickers workflow; `app.yaml` с `min_text_chars_per_page` + `issuer_trim_max_chars`; `metrics.yaml.template`; `unrar` → опциональный | нет |

**Действия оператора перед первым `edx update`** на новой инсталляции:

1. `python tools/find_e_disclosure_ids.py --tickers <X,Y,Z>` — подсказать
   правильные `e_disclosure_id` (для всех записей с `REPLACE_ME` в
   `config/tickers.yaml`).
2. Вписать выбранные id руками.
3. `python tools/validate_tickers.py --strict` — убедиться, что у каждого
   тикера хотя бы один из `type=3/4/5` отдаёт реальные публикации.

**Внешние зависимости:** сайт защищён ServicePipe (TLS-fingerprint +
JS-challenge). Способа два:

- **Быстро, нестабильно:** оставить `app.discoverer.http_backend: httpx`
  (дефолт), вручную обновлять cookies в `discoverer.cookies` каждые
  несколько часов. Работает на «один прогон руками» или короткое окно.
- **Надёжно, для cron-прогона на VPS:** Patch 23 — `http_backend:
  playwright`. Запускает headless-Chromium один раз за прогон,
  проходит JS-challenge в браузере, и все запросы Discoverer +
  Downloader идут через TCP/TLS-стек Chromium (JA3 совпадает,
  cookies остаются валидными). Установка:
  ```bash
  pip install '.[playwright]'
  playwright install chromium
  playwright install-deps chromium     # Linux only — apt-ставит libnss и т.д.
  ```
  Затем в `config/app.yaml` под `discoverer:` поставьте
  `http_backend: playwright` и забудьте про ручное обновление cookies.

Перечень и принципы серии (мульти-эмитентные фикстуры, требование
источника к каждому синониму метрики и пр.) — в
[`prompts/README.md`](prompts/README.md).
