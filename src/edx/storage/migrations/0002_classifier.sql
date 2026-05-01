-- ТЗ §5.1, §6: фиксируем «ведущий» документ публикации за период (МСФО > РСБУ).
-- На этом этапе колонка только заводится; PDF Classifier и Metric Extractor
-- заполняют её на следующих стадиях.

ALTER TABLE documents ADD COLUMN is_primary_for_period INTEGER NOT NULL DEFAULT 0;
