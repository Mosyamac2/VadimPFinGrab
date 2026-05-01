# Промпт 13. Репликация на Google Drive

## Цель
После каждого успешного запуска перезаписать `e-disclosure.xlsx` в указанной папке Google Drive. Сохранять одну и ту же ссылку (использовать `update`, а не `create`). Опционально — датированный снапшот в `archive/`.

## Контекст из ТЗ
- Раздел 10.4: «При перезаписи сохраняется одна и та же ссылка ... используется update, а не create».
- Раздел 13: OAuth 2.0 с refresh token, ключи в `.env`.
- Раздел 7.2: внешние интеграции через интерфейсы-абстракции.

## Задачи
1. Добавить зависимости: `google-api-python-client`, `google-auth`, `google-auth-oauthlib`.
2. Создать `src/edx/providers/storage/`:
   - `base.py`:
     ```python
     class CloudStorageProvider(Protocol):
         def upsert_file(self, local_path: Path, remote_folder_id: str,
                         remote_name: str, *, archive: bool) -> RemoteFileInfo: ...
     ```
   - `google_drive.py`:
     - Аутентификация: `Credentials.from_authorized_user_info({...})` с `client_id`, `client_secret`, `refresh_token`. Без интерактивных flow в рантайме — рефреш-токен заводится один раз отдельной утилитой (см. п.4).
     - `upsert_file`:
       - ищет файл `remote_name` в `remote_folder_id` через `files.list(q="name = '...' and '<folder>' in parents and trashed=false")`.
       - если найден — `files.update(media_body=...)` (сохраняет file_id и ссылку).
       - если не найден — `files.create(...)` с указанием `parents=[remote_folder_id]`.
       - если `archive=True` — дополнительно копирует в подпапку `archive/`, имя — `e-disclosure-YYYY-MM-DD-HHMM.xlsx` (создать подпапку при отсутствии).
       - возвращает `RemoteFileInfo(file_id, web_view_link, updated_at)`.
3. Создать `src/edx/stages/writer/replicator.py`:
   - `ReplicatorService.run(local_excel_path)`:
     - читает конфиг `app.yaml → google_drive: {folder_id, archive: bool}`;
     - вызывает `CloudStorageProvider.upsert_file`;
     - пишет в БД (новая колонка в `runs`: `excel_drive_file_id`, `excel_drive_link` — миграция `0005_runs_drive.sql`).
4. CLI-утилита `edx auth google-drive`:
   - запускает локальный OAuth-flow (`InstalledAppFlow.run_local_server(port=0)` с scopes `https://www.googleapis.com/auth/drive.file`);
   - печатает refresh_token в stdout с инструкцией: «вставьте значение в `.env` как `GOOGLE_OAUTH_REFRESH_TOKEN=...`».
   - НЕ записывает токен на диск автоматически — оператор сам помещает его в `.env` (минимум сюрпризов с правами и хранением).
5. Опции: если `app.yaml → google_drive.enabled: false` — стадия пропускается, в логе предупреждение «replication disabled».

## Тесты, которые должны проходить
- Юнит-тест `google_drive.GoogleDriveProvider` с замокированным `googleapiclient.discovery.build`:
  - первая загрузка → вызов `files.create` с правильными аргументами;
  - повторная загрузка → `files.list` находит существующий → `files.update`;
  - `archive=True` → дополнительный `files.create` в подпапке `archive/`;
  - подпапка `archive/` создаётся при первом запросе и переиспользуется при повторе.
- Юнит-тест `ReplicatorService`:
  - при `enabled=false` стадия не вызывает Drive-провайдер;
  - после успешной репликации в `runs` обновлены поля `excel_drive_file_id`, `excel_drive_link`.
- Никаких реальных вызовов Google в `pytest`.

## Definition of Done
- `edx auth google-drive` (ручной шаг оператора) работает на реальной учётке и печатает refresh_token.
- После полного прогона пайплайна (`edx update`) файл `e-disclosure.xlsx` обновляется в Google Drive **по той же ссылке**, что и в прошлый раз.
- Опциональный архив включается флагом конфига без правки кода.
