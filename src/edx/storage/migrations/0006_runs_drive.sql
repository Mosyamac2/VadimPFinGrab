-- ТЗ §10.4: репликация Excel-витрины на Google Drive с одной и той же
-- ссылкой между прогонами. Сохраняем file_id и web_view_link на уровне
-- запуска, чтобы оператор видел в edx status, куда легла последняя витрина.

ALTER TABLE runs ADD COLUMN excel_drive_file_id TEXT;
ALTER TABLE runs ADD COLUMN excel_drive_link TEXT;
