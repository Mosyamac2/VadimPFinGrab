-- ТЗ §11.2: публикации с покрытием < threshold помечаются как incomplete
-- и попадают в отдельный отчёт о проблемах извлечения. Колонка обновляется
-- стадией Metric Extractor; Validator затем читает её и пишет в qa_issues.

ALTER TABLE publications ADD COLUMN is_incomplete INTEGER NOT NULL DEFAULT 0;
