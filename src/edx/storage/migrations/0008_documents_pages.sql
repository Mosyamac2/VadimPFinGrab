-- Patch 18: per-page text/scan classification — required for hybrid PDFs
-- like banking RSBU forms 0409806/0409807, where the first pages carry
-- machine-readable text and the regulator forms further in are scans.
-- Document-level ``is_machine_readable`` (kept for back-compat as the
-- aggregate ``OR`` of page kinds) silently dropped scan pages on those
-- documents; the per-page list now lets the Text Extractor OCR only the
-- pages that need it.
--
-- All columns are nullable so existing rows stay valid; the Classifier
-- backfills them on the next run.

ALTER TABLE documents ADD COLUMN pages_classification TEXT;
ALTER TABLE documents ADD COLUMN text_pages_count INTEGER;
ALTER TABLE documents ADD COLUMN scan_pages_count INTEGER;
