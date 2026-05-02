-- Patch 17: type-of-report code and reporting period derived from the listing
-- page (e-disclosure /portal/files.aspx?id=X&type=Y), not guessed from PDF text.
-- All columns nullable so existing rows stay valid; new Discoverer fills them.

ALTER TABLE publications ADD COLUMN report_type_code INTEGER;
ALTER TABLE publications ADD COLUMN report_type_label TEXT;
ALTER TABLE publications ADD COLUMN reporting_period_year INTEGER;
ALTER TABLE publications ADD COLUMN reporting_period_type TEXT
    CHECK(reporting_period_type IN ('Q1','Q2','Q3','Q4','H1','H2','9M','FY')
          OR reporting_period_type IS NULL);

CREATE INDEX idx_publications_period
    ON publications(ticker, reporting_period_year, reporting_period_type);
