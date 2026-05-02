# Промпт 23. Playwright HTTP-бэкенд для обхода ServicePipe (JA3)

## Цель
Подключить headless-Chromium через Playwright как альтернативный HTTP-бэкенд для стадий Discoverer и Downloader. Без него боевой `edx update` останавливается на ServicePipe-challenge: cookies, выданные браузеру, отвергаются под TLS-fingerprint Python-`httpx`. Playwright выполняет JS-challenge внутри настоящего Chromium и переиспользует тот же сетевой стек для всех последующих запросов — JA3 совпадает с тем, под которым cookies были выписаны.

## Контекст
- `PLAN_e-disclosure_parser_v2.md` § «Внешние зависимости» — единственный надёжный путь обхода ServicePipe.
- v2-серия (Patch 16–22) полностью функциональна на синтетических фикстурах; на live-сайте ломается ровно об ServicePipe → этот патч закрывает последнюю blocker-проблему.
- **Зависимости:** опирается на текущий `EDisclosureClient` (наследуется от него для совместимости типов); не зависит от других патчей серии.

## Задачи

### 1. `src/edx/http/playwright_client.py` (новый файл)
- Класс `PlaywrightEDisclosureClient(EDisclosureClient)` — подкласс с переопределёнными `__aenter__/__aexit__/close/get/download`. Базовый `__init__` вызывается, но httpx-state закрывается сразу в `__aenter__` и не используется.
- Импорт `playwright.async_api` отложен **внутрь** `__aenter__` — пакет опциональный, и обычный pytest-suite не должен требовать его установки.
- Bootstrap: при `__aenter__` открывается одна страница (`base_url + "/"`) через `page.goto(..., wait_until="networkidle")` — JS-challenge выполняется в браузере, контекст набирает валидные cookies от Chromium-handshake.
- `get(url)` использует `context.request.get(...)` — это Playwright-овский HTTP-клиент, который шарит cookie jar и TCP/TLS стек с браузером. Возвращает `_PlaywrightResponse` (frozen dataclass с `status_code`, `text`, `content`, `headers`) — имитирует подмножество `httpx.Response`, которое реально читает downstream-код.
- `download(url, target)` — то же через `context.request.get`, тело пишется в `{target}.partial`, потом `os.replace`. SHA-256 считается на ходу.
- Сохраняется `aiolimiter`-пейсинг (1 RPS) от родителя.
- `respect_robots`: для Playwright-бэкенда родительский RobotsCache (на httpx) бесполезен; флаг сохраняется в `_desired_respect_robots`, и при `aenter` логируется `robots_check_disabled` если оператор его оставил false. Полноценная проверка robots.txt через Playwright — отдельная задача, не в этом патче.

### 2. `src/edx/config/app_config.py`
- В `DiscovererConfig` добавить:
  ```python
  http_backend: Literal["httpx", "playwright"] = "httpx"
  ```
- Дефолт `httpx` — обратная совместимость; новое поведение под флаг.

### 3. `src/edx/http/factory.py` (новый)
- Функция `build_http_client(settings, *, transport=None) -> EDisclosureClient`:
  - При `cfg.http_backend == "playwright"` — отложенный импорт `PlaywrightEDisclosureClient` и его конструктор; параметр `transport` игнорируется.
  - Иначе — обычный `EDisclosureClient` с теми же knobs, что и раньше.
- Экспортировать через `src/edx/http/__init__.py`.

### 4. Centralised dispatch
- `src/edx/stages/discoverer/factory.py:build_edisclosure_client` — стать тонким алиасом над `build_http_client` (back-compat).
- `src/edx/cli.py` — два прямых вызова `EDisclosureClient(...)` (под `edx update` и под `edx download`) заменить на `async with build_http_client(settings) as client:`. Удалить локальные `from edx.http.client import EDisclosureClient, build_user_agent` если становятся неиспользуемыми.

### 5. `pyproject.toml`
- Добавить опциональную группу:
  ```toml
  [project.optional-dependencies]
  playwright = ["playwright>=1.50"]
  ```
- Установка для оператора: `pip install '.[playwright]' && playwright install chromium && playwright install-deps chromium`.

### 6. `config/app.yaml`
- Под `discoverer:` дописать `http_backend: httpx` явно с комментарием про лекарство от ServicePipe (см. README).

### 7. Тесты
- `tests/http/test_http_factory.py`:
  - `test_build_http_client_returns_httpx_by_default`: дефолтный конфиг даёт `EDisclosureClient` (точный тип, не подкласс).
  - `test_build_http_client_returns_playwright_when_configured`: после `settings.app.discoverer.http_backend = "playwright"` фабрика отдаёт `PlaywrightEDisclosureClient`. Сам `__init__` не дёргает Playwright (тест проходит без установленного пакета).
  - `test_playwright_client_get_outside_context_raises`: вызов `get` до `__aenter__` бросает понятный `RuntimeError("…async with…")`, не молчаливо None.
  - `test_playwright_module_imports_without_playwright_installed`: модуль импортируется на хосте без `playwright` (импорт пакета — только в `__aenter__`).
  - `test_playwright_aenter_without_package_installed_raises_runtime_error`: skip когда `playwright` установлен; иначе `async with` падает с понятной install-инструкцией.
- Реальный browser-тест **не требуется** — пакет тяжёлый, и хост CI обычно не имеет браузера. Live-проверка остаётся ручной (см. DoD).

### 8. README + USER_GUIDE
- README §«Внешние зависимости»: переписать с двумя путями — «httpx + ручные cookies» (быстро, нестабильно) и «Playwright» (надёжно, для cron). Указать `pip install '.[playwright]'` + `playwright install chromium` + `playwright install-deps chromium`.
- USER_GUIDE: новый раздел «ServicePipe / headless Chromium» перед «Когда нужна помощь разработчика». Пошагово: установка, переключение `http_backend` в `app.yaml`, цена (RAM ~250 МБ, диск ~300 МБ). Troubleshooting: проверить наличие `playwright_client_started` в логе, доставить отсутствующие libs руками если `install-deps` промахнулся.
- USER_GUIDE «Если что-то не работает»: добавить строку для симптома «200 OK, body ≈ 1700, `discoverer_no_publications_for_type` на каждом запросе» → переключить на Playwright.

## Тесты, которые должны проходить
- Все 5 факторных тестов выше зелёные **и без**, и с установленным `playwright`.
- Полный `make lint typecheck test` зелёный.
- Существующие тесты `EDisclosureClient` (rate-limit, retry, robots) не сломаны.

## Definition of Done
- Дефолтный `edx update` с `http_backend: httpx` ведёт себя как до Patch 23.
- Переключение `http_backend: playwright` + установленный Playwright + Chromium → на боевом e-disclosure `body_bytes` подскакивает с ~1700 до десятков-сотен КБ, `discoverer_no_publications_for_type` пропадает, `ticker_type_discovered` логирует `found=N new=N`.
- На хосте без Playwright `async with build_http_client(...)` падает с понятным сообщением, указывающим точную команду `pip install …` + `playwright install chromium`.
- README + USER_GUIDE объясняют обе ветки и переключение между ними.
