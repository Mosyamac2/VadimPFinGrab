# Промпт 01. Каркас проекта

## Цель
Поднять минимально работающий скелет Python-проекта под Linux с правильной структурой каталогов, заготовкой CLI, структурированным логированием и инфраструктурой тестов. На этом этапе бизнес-логики ещё нет — только «рельсы».

## Контекст из ТЗ
- Раздел 7 (архитектура) — модульная структура, оркестратор, изолированные стадии.
- Раздел 8 (стек): Python 3.11+, structlog, Pydantic, pytest подразумевается, целевая ОС — Linux.
- Раздел 10.1 — фиксированная локальная структура каталогов.

## Задачи
1. Создать `pyproject.toml` (PEP 621) с метаданными проекта `e-disclosure-extractor`, Python `>=3.11`, точкой входа `edx = edx.cli:main`. Использовать `setuptools` как build-backend.
2. Создать структуру:
   ```
   src/edx/
       __init__.py
       cli.py
       logging_setup.py
       stages/__init__.py
       providers/__init__.py
       storage/__init__.py
   tests/
       __init__.py
       test_smoke.py
   config/                 # пустой, .gitkeep
   data/raw/               # .gitkeep
   data/processed/         # .gitkeep
   output/                 # .gitkeep
   logs/                   # .gitkeep
   ```
3. В `cli.py` сделать на `argparse` две команды (заглушки, печатают своё имя через structlog):
   - `update`
   - `run --full-reload`
4. В `logging_setup.py` настроить structlog: JSON-рендерер, ISO-таймстемпы, уровень из переменной окружения `EDX_LOG_LEVEL` (дефолт `INFO`), вывод в stdout и в `logs/pipeline.log` через стандартный `logging` с `RotatingFileHandler` (10 МБ × 5 файлов).
5. Добавить `.gitignore` (Python + `.env` + `data/raw/*` + `data/processed/*` + `output/*` + `logs/*`, оставив `.gitkeep`).
6. Добавить `.env.example` с заглушками всех будущих ключей: `ANTHROPIC_API_KEY=`, `OPENROUTER_API_KEY=`, `GOOGLE_OAUTH_CLIENT_ID=`, `GOOGLE_OAUTH_CLIENT_SECRET=`, `GOOGLE_OAUTH_REFRESH_TOKEN=`, `YANDEX_VISION_OCR_KEY=`.
7. Установить dev-зависимости: `pytest`, `pytest-cov`, `ruff`, `mypy`, `structlog`. Раннер-зависимости пока — только `structlog` и `pydantic>=2`.
8. В `tests/test_smoke.py`:
   - тест на импорт `edx`;
   - тест запуска CLI с `--help` через `subprocess` (должен выйти с кодом 0 и упомянуть `update` и `run`);
   - тест, что `logging_setup.configure()` создаёт файл `logs/pipeline.log` после первого лог-сообщения (использовать tmp-каталог через monkeypatch).
9. В корне создать `Makefile` с целями: `install`, `test`, `lint` (ruff), `typecheck` (mypy strict), `clean`.

## Тесты, которые должны проходить
```
make install
make lint
make typecheck
make test
```
- `pytest -q` — все тесты зелёные.
- `edx --help` запускается из активированного venv.
- `ruff check` без ошибок.
- `mypy src` без ошибок (на этом этапе кода мало, должно быть тривиально).

## Definition of Done
- Запуск `edx update` печатает structlog-лог в JSON и завершается с кодом 0.
- В `logs/pipeline.log` появляется одна строка про вызов команды.
- В git нет сгенерированных артефактов (`logs/*.log` в `.gitignore`).
- Никаких заглушек бизнес-логики — только CLI-арматура и логирование.
