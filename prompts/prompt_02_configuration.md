# Промпт 02. Конфигурация и секреты

## Цель
Реализовать загрузку и валидацию всех YAML-конфигов и `.env` с Pydantic, чтобы любой последующий код брал параметры из единой типизированной структуры. Никаких «магических чисел» в коде.

## Контекст из ТЗ
- Раздел 9 (конфигурация): шесть YAML-файлов + `.env`, валидация Pydantic, перечитывание на старте каждого цикла.
- Раздел 13 (секреты): `.env` через Pydantic Settings, никакого прямого `os.environ`.
- Раздел 5 (метрики), 6 (события), 8 (стек) — список и форматы конфигов.

## Задачи
1. Добавить зависимости: `pydantic-settings`, `pyyaml`, `python-dotenv` (опционально — Pydantic Settings и так умеет).
2. Создать модуль `src/edx/config/` со схемами:
   - `app_config.py` → `AppConfig` (пути из раздела 10.1, расписание, режим инкрементальный/полный, глубина backfill в годах = 3, дефолтное время cron `04:00`).
   - `tickers_config.py` → `TickerEntry` (`ticker`, `e_disclosure_id`, `inn`, `ogrn`, `name`, `priority_override` — опционально), `TickersConfig` (список).
   - `metrics_config.py` → `MetricSpec` (`canonical_name`, `synonyms_ifrs`, `synonyms_rsbu`, `unit`, `currency`, `formula` Optional[str]), `MetricsConfig` (`metrics: list[MetricSpec]`, `reporting_priority: list[Literal["IFRS","RSBU"]]`).
   - `event_types_config.py` → `EventTypesConfig` (список с `code`, `display_name`, `aliases`).
   - `llm_config.py` → `LLMConfig` (`primary: AnthropicProviderConfig`, `fallback: OpenRouterProviderConfig`, общие лимиты: `max_tokens`, `temperature`, `request_timeout_s`, `max_retries`, `concurrency`).
   - `ocr_config.py` → `OCRConfig` (`engine: Literal["tesseract","yandex_vision","google_vision"]`, `tesseract_langs: list[str]`, опции для облачного OCR).
3. Создать `src/edx/config/loader.py` с функцией `load_all(config_dir: Path) -> AppSettings`, где `AppSettings` агрегирует все Pydantic-модели + `Secrets`. Перечитывание делать каждый раз — без кеширования (раздел 9.2).
4. Реализовать `Secrets` через `pydantic_settings.BaseSettings` с источником `.env`. Поля: `anthropic_api_key`, `openrouter_api_key`, `google_oauth_client_id`, `google_oauth_client_secret`, `google_oauth_refresh_token`, `yandex_vision_ocr_key` — все `SecretStr | None`.
5. Создать примеры конфигов в `config/`:
   - `app.yaml` (с дефолтными путями и расписанием).
   - `tickers.yaml` — заголовок-комментарий + 3 примера эмитентов (Сбербанк, Газпром, Лукойл) с заглушечными e-disclosure ID. Полный Top-50 — отдельной задачей оператора.
   - `metrics.yaml` — пять метрик из раздела 5.2 с реальными синонимами (Revenue/Выручка, EBITDA, Net Income/Чистая прибыль, Total Assets/Активы, Total Debt/Долг).
   - `event_types.yaml` — справочник из раздела 6 (дивиденды, смена менеджмента, M&A, существенная сделка, корпоративное действие, изменение в составе акционеров, прочее).
   - `llm.yaml` — приоритет Anthropic, fallback OpenRouter, модель Claude Sonnet 4.6 (`claude-sonnet-4-6`), таймауты, ретраи.
   - `ocr.yaml` — Tesseract `rus+eng` по умолчанию.
6. На старте `edx update` / `edx run` загружать конфиг и валидировать; при ошибке — внятное сообщение и exit code 2.
7. Добавить CLI-команду `edx config check` — печатает все загруженные значения (секреты замаскировать).

## Тесты, которые должны проходить
- Загрузка эталонных YAML из репозитория не падает.
- Подмена YAML с лишним полем → `ValidationError`, exit code 2 в CLI, в логе — путь к файлу и поле.
- Подмена YAML с недопустимым `reporting_priority` (например, `["GAAP"]`) → `ValidationError`.
- `edx config check` маскирует секреты (`***`).
- Pydantic-модели имеют 100% покрытие в тестах через `pytest`.
- `make lint typecheck test` — зелёные.

## Definition of Done
- Все строковые литералы из ТЗ (имена показателей, типы событий, приоритеты типов отчётности, дефолтное время cron) присутствуют в YAML и **не дублируются** в Python-коде.
- Любой следующий модуль получает конфиг через DI (`AppSettings`), а не читает YAML самостоятельно.
- В README кратко описан формат каждого YAML.
