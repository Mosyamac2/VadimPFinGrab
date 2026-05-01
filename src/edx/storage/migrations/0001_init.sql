-- ТЗ §10.2: state-БД и витрина в одном файле SQLite.
-- Миграция применяется одной транзакцией; идемпотентность обеспечивается
-- таблицей schema_migrations, которую создаёт сам Database.migrate().

CREATE TABLE tickers (
    ticker          TEXT PRIMARY KEY,
    e_disclosure_id TEXT NOT NULL,
    inn             TEXT,
    ogrn            TEXT,
    name            TEXT NOT NULL,
    added_at        TEXT NOT NULL
);

CREATE TABLE publications (
    publication_id   TEXT PRIMARY KEY,
    ticker           TEXT NOT NULL REFERENCES tickers(ticker),
    publication_type TEXT NOT NULL CHECK(publication_type IN ('report','event')),
    publication_date TEXT NOT NULL,
    source_url       TEXT NOT NULL,
    file_hash        TEXT,
    status           TEXT NOT NULL CHECK(status IN (
        'discovered','downloaded','unpacked','classified',
        'extracted','validated','written','failed','skipped'
    )),
    last_error       TEXT,
    discovered_at    TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX idx_publications_ticker_date ON publications(ticker, publication_date);

CREATE TABLE documents (
    document_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id      TEXT NOT NULL REFERENCES publications(publication_id) ON DELETE CASCADE,
    relative_path       TEXT NOT NULL,
    mime_type           TEXT,
    reporting_standard  TEXT CHECK(reporting_standard IN ('IFRS','RSBU','OTHER') OR reporting_standard IS NULL),
    report_form         TEXT,
    is_machine_readable INTEGER,
    page_count          INTEGER,
    file_hash           TEXT NOT NULL,
    UNIQUE(publication_id, relative_path)
);

CREATE TABLE metrics (
    metric_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker             TEXT NOT NULL,
    reporting_date     TEXT NOT NULL,
    period_type        TEXT NOT NULL CHECK(period_type IN ('Q1','Q2','Q3','Q4','H1','H2','9M','FY')),
    reporting_standard TEXT NOT NULL CHECK(reporting_standard IN ('IFRS','RSBU')),
    metric_name        TEXT NOT NULL,
    value              REAL,
    currency           TEXT NOT NULL,
    unit               TEXT NOT NULL,
    source_document_id INTEGER REFERENCES documents(document_id),
    qa_warning         TEXT,
    extracted_at       TEXT NOT NULL,
    UNIQUE(ticker, reporting_date, period_type, reporting_standard, metric_name)
);

CREATE INDEX idx_metrics_ticker_date ON metrics(ticker, reporting_date);

CREATE TABLE events (
    event_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                TEXT NOT NULL,
    event_date            TEXT NOT NULL,
    publication_date      TEXT NOT NULL,
    event_type            TEXT NOT NULL,
    summary               TEXT NOT NULL,
    key_params_json       TEXT,
    source_url            TEXT NOT NULL,
    source_publication_id TEXT REFERENCES publications(publication_id),
    extracted_at          TEXT NOT NULL,
    UNIQUE(source_publication_id)
);

CREATE INDEX idx_events_ticker_date ON events(ticker, event_date);

CREATE TABLE runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','partial')),
    mode          TEXT NOT NULL CHECK(mode IN ('update','full_reload')),
    stats_json    TEXT,
    error_summary TEXT
);
