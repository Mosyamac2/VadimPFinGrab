# Промпт 05. Downloader и Unpacker

## Цель
Скачать обнаруженные публикации в локальное blob-хранилище и распаковать архивы (RAR/ZIP), составив инвентарь содержимого для последующих стадий.

## Контекст из ТЗ
- Раздел 7.1, п.2 (Downloader) и п.3 (Unpacker).
- Раздел 10.1: путь `data/raw/{ticker}/{publication_id}/`.
- Раздел 12.2: идемпотентность через хеш файла + статус.
- Раздел 8: `rarfile` + системный `unrar`.

## Задачи

### Downloader
1. Создать `src/edx/stages/downloader/`:
   - `service.py`: `DownloaderService.run(publications) -> list[DownloadedPublication]`.
   - Для каждой публикации со статусом `discovered`:
     - Создать каталог `data/raw/{ticker}/{publication_id}/`.
     - Скачать файл по `source_url` через `EDisclosureClient` (стримом, чтобы не держать в памяти крупные RAR).
     - Посчитать SHA-256 файла, записать в `publications.file_hash`.
     - Если файл уже есть и его хеш совпадает с записанным в БД — пропустить с лог-сообщением `skipped: identical_hash`, статус не менять.
     - При успехе перевести статус публикации в `downloaded`.
2. Поддержать публикации, у которых исходный URL — это HTML-страница со ссылками на несколько файлов (часто бывает у событий). Сохранять все ссылочные файлы в подкаталог.
3. Лимиты: одновременная загрузка — из `app.yaml → downloader.concurrency`, дефолт `4`. Использовать `asyncio.Semaphore`.
4. На частичных загрузках (разрыв соединения) — удалять `*.partial`-файл и ретраить.

### Unpacker
1. Добавить зависимость `rarfile`. В README.md прописать требование к окружению: `apt-get install unrar`.
2. Создать `src/edx/stages/unpacker/`:
   - `service.py`: `UnpackerService.run(publications)`.
   - Поддерживаемые форматы: `.rar`, `.zip`. Любые иные расширения — пропустить с warning.
   - Распаковать в `data/raw/{ticker}/{publication_id}/_unpacked/`.
   - Для каждого извлечённого файла создать запись в `documents` со статусом «не классифицирован» (`reporting_standard = NULL`, `is_machine_readable = NULL`):
     - `relative_path` — относительно `data/raw/{ticker}/{publication_id}/`.
     - `mime_type` — через `mimetypes.guess_type`.
     - `file_hash` — SHA-256.
   - При успехе — перевести публикацию в статус `unpacked`.
3. Защита от zip-bomb / path traversal: проверять, что распакованные пути не выходят за пределы целевого каталога; общий лимит распакованного объёма — из конфига (`app.yaml → unpacker.max_unpacked_mb`, дефолт 500). При превышении — статус `failed` с описанием.

## Тесты, которые должны проходить
- Юнит-тесты Downloader (с `httpx.MockTransport`):
  - первая загрузка пишет файл и хеш;
  - повторная загрузка с тем же контентом пропускается;
  - частичная загрузка (исключение посередине) не оставляет «битых» файлов в `data/raw/`.
- Юнит-тесты Unpacker:
  - распаковка тестового `.zip` — все файлы появляются в `documents` с корректным хешем.
  - попытка распаковки `.zip` с путями `../etc/passwd` → `failed`, ничего не записано на диск за пределами рабочего каталога.
  - распаковка `.rar` — поведение покрыто тестом, скипающимся, если в окружении нет `unrar` (`pytest.importorskip` / `shutil.which`).
- Тесты используют небольшие фикстуры в `tests/fixtures/archives/`.

## Definition of Done
- На вход поступает список `DiscoveredPublication`, после прогона стадий — на диске лежат распакованные файлы, в `documents` — записи на каждый файл, в `publications` — статус `unpacked`.
- `edx unpack --publication-id <id>` запускается изолированно.
- Никаких лишних повторных скачиваний при повторном запуске.
