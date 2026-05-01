# Промпт 04. HTTP-клиент и Discoverer (e-disclosure.ru)

## Цель
Реализовать аккуратный HTTP-слой и стадию `Discoverer`, которая обходит карточки эмитентов на e-disclosure.ru и формирует список новых публикаций (отчёты + сообщения о существенных фактах) с момента последней успешной обработки.

## Контекст из ТЗ
- Раздел 2: основной источник, типы контента, без авторизации, соблюдение `robots.txt`, rate limit, паузы, User-Agent.
- Раздел 7.1, п. 1: контракт стадии — на вход список тикеров, на выход — список новых публикаций.
- Раздел 4: инкрементальный режим — публикации позже даты последней успешной обработки.

## Задачи
1. Добавить зависимости: `httpx`, `selectolax` (или `beautifulsoup4` — на выбор, но один), `tenacity` для ретраев, `aiolimiter` для rate-limit.
2. Создать `src/edx/http/client.py`:
   - `EDisclosureClient` с настраиваемыми `base_url`, `user_agent` (включает контактный email из конфига `app.yaml` → `contact_email`), `requests_per_second` (дефолт `1.0`), `request_timeout_s`.
   - Через `aiolimiter.AsyncLimiter` гарантировать **не больше N запросов в секунду** на домен.
   - Через `tenacity` ретраить только идемпотентные GET-ы при `httpx.TransportError`, 5xx, 429 (с уважением к `Retry-After`). Лимит ретраев — из конфига.
   - В каждом ответе логировать URL, статус, размер тела, время ответа (structlog).
3. Создать `src/edx/http/robots.py`:
   - При первом обращении к домену загрузить `robots.txt`, кешировать на время процесса.
   - Метод `is_allowed(url)` — `urllib.robotparser`. Если `disallow` — выбросить `RobotsDisallowedError`.
4. Создать модуль `src/edx/stages/discoverer/`:
   - `parser.py`: чистые парсеры HTML карточки эмитента и страницы публикаций (без I/O). Извлечь:
     - `publication_id` (стабильный — берётся из URL или сгенерирован хешем url+date),
     - `publication_type` (`report` / `event`),
     - `publication_date` (ISO-8601),
     - `source_url`,
     - `title`.
   - `service.py`: `DiscovererService.run(tickers, since: dict[ticker, date]) -> list[DiscoveredPublication]`.
     - Для каждого тикера получает страницы карточки эмитента.
     - Возвращает только публикации с `publication_date > since[ticker]` (для несуществующих в state — backfill 3 года, т.е. `since = today - 3y`).
     - Записывает все найденные публикации в `publications` со статусом `discovered`.
   - `factory.py`: фабрика, собирающая сервис из `AppSettings` и репозиториев.
5. Покрыть тестами на **зафиксированных HTML-фикстурах** (положить 2-3 примера карточки эмитента в `tests/fixtures/edisclosure/`):
   - Парсер корректно вытаскивает поля.
   - Граничные кейсы: пустая карточка, публикации без даты — пропускаются с warning.
6. Добавить флаг конфига `app.yaml → discoverer.respect_robots: bool` (дефолт `true`); при `false` — лог-warning о том, что соблюдение `robots.txt` отключено.
7. CLI: `edx discover --ticker SBER` — изолированный запуск стадии для отладки (раздел 7.2: «любая стадия может быть запущена изолированно»).

## Тесты, которые должны проходить
- Юнит-тесты парсера на фикстурах: точное совпадение полей.
- Тест rate-limit: 5 запросов с лимитом 2 rps занимают не меньше 2 секунд (`pytest.approx`).
- Тест ретраев: мок-сервер отвечает 503 дважды, потом 200 — клиент возвращает 200, делает ровно 2 ретрая.
- Тест robots: `Disallow: /` → `RobotsDisallowedError`.
- Тест инкрементальности: при наличии записи в `publications` с `publication_date = D` повторный discover не возвращает её.
- Все тесты — **без сетевых вызовов** (использовать `httpx.MockTransport` или `respx`).

## Definition of Done
- `edx discover --ticker SBER` (или любой реальный e-disclosure ID, прописанный в `tickers.yaml`) на реальном сайте отрабатывает без ошибок и записывает строки в `publications`. Этот ручной чек делает оператор; автоматический сетевой тест не обязателен.
- Никаких сетевых вызовов в `pytest`.
- Парсер изолирован от I/O и легко расширяется на новые поля.
