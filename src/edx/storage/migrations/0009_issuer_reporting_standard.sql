-- Patch 21: widen the ``reporting_standard`` CHECK on both ``documents``
-- and ``metrics`` so the Issuer Report (type=5) source can flow through
-- the pipeline as ``ISSUER`` instead of being silently squashed to RSBU
-- by the Patch-19 Metric Extractor compatibility shim.
--
-- SQLite cannot ALTER an existing CHECK constraint, so we recreate the
-- table with the wider CHECK, copy the rows, drop the original, and
-- rename. Foreign keys and indexes are recreated explicitly.
--
-- This migration is destructive only on the table definition; row data
-- is preserved 1:1. ``defer_foreign_keys`` postpones FK checks to the
-- COMMIT (Database.migrate() runs each migration in a transaction); by
-- then the new ``documents`` table holds the same document_ids the
-- ``metrics`` rows reference, so the constraint stays satisfied.

PRAGMA defer_foreign_keys = ON;

-- documents -----------------------------------------------------------
CREATE TABLE documents_new (
    document_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id        TEXT NOT NULL REFERENCES publications(publication_id) ON DELETE CASCADE,
    relative_path         TEXT NOT NULL,
    mime_type             TEXT,
    reporting_standard    TEXT CHECK(reporting_standard IN ('IFRS','RSBU','OTHER','ISSUER','ANNUAL') OR reporting_standard IS NULL),
    report_form           TEXT,
    is_machine_readable   INTEGER,
    page_count            INTEGER,
    file_hash             TEXT NOT NULL,
    is_primary_for_period INTEGER NOT NULL DEFAULT 0,
    text_extract_path     TEXT,
    pages_classification  TEXT,
    text_pages_count      INTEGER,
    scan_pages_count      INTEGER,
    UNIQUE(publication_id, relative_path)
);

INSERT INTO documents_new (
    document_id, publication_id, relative_path, mime_type, reporting_standard,
    report_form, is_machine_readable, page_count, file_hash,
    is_primary_for_period, text_extract_path, pages_classification,
    text_pages_count, scan_pages_count
)
SELECT
    document_id, publication_id, relative_path, mime_type, reporting_standard,
    report_form, is_machine_readable, page_count, file_hash,
    is_primary_for_period, text_extract_path, pages_classification,
    text_pages_count, scan_pages_count
FROM documents;

DROP TABLE documents;
ALTER TABLE documents_new RENAME TO documents;

-- metrics --------------------------------------------------------------
CREATE TABLE metrics_new (
    metric_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker             TEXT NOT NULL,
    reporting_date     TEXT NOT NULL,
    period_type        TEXT NOT NULL CHECK(period_type IN ('Q1','Q2','Q3','Q4','H1','H2','9M','FY')),
    reporting_standard TEXT NOT NULL CHECK(reporting_standard IN ('IFRS','RSBU','ISSUER')),
    metric_name        TEXT NOT NULL,
    value              REAL,
    currency           TEXT NOT NULL,
    unit               TEXT NOT NULL,
    source_document_id INTEGER REFERENCES documents(document_id),
    qa_warning         TEXT,
    extracted_at       TEXT NOT NULL,
    UNIQUE(ticker, reporting_date, period_type, reporting_standard, metric_name)
);

INSERT INTO metrics_new SELECT * FROM metrics;
DROP TABLE metrics;
ALTER TABLE metrics_new RENAME TO metrics;

CREATE INDEX idx_metrics_ticker_date ON metrics(ticker, reporting_date);
