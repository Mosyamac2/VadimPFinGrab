-- ТЗ §11: агрегированный отчёт о проблемах извлечения для ручного разбора.
-- Каждая запись — одно нарушенное правило в публикации (балансовое
-- уравнение, знак, YoY, валюты, единицы, completeness и т.п.).

CREATE TABLE qa_issues (
    issue_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id TEXT NOT NULL REFERENCES publications(publication_id) ON DELETE CASCADE,
    ticker         TEXT NOT NULL,
    code           TEXT NOT NULL,
    message        TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE INDEX idx_qa_issues_publication ON qa_issues(publication_id);
CREATE INDEX idx_qa_issues_ticker ON qa_issues(ticker);
