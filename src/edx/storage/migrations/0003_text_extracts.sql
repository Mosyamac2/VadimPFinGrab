-- ТЗ §7.1 п.5: путь к JSON-выгрузке текста (нативно из pymupdf или OCR).
-- Заполняется TextExtractorService после успешного извлечения.

ALTER TABLE documents ADD COLUMN text_extract_path TEXT;
