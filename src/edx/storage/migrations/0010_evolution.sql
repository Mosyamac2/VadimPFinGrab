-- Patch 38: self-evolution loop bookkeeping.
--
-- evolution_ticks    — журнал тиков (один тик = batch из 3 компаний из
--                      e-disclosure-companies.csv, прогон пайплайна,
--                      опциональный вызов Claude Code и git auto-merge).
-- evolution_skiplist — компании, которые трижды подряд не получилось
--                      починить (Picker их пропускает до ручного reset),
--                      или которые пересекаются с MOEX-тикерами в
--                      основном config/tickers.yaml.

CREATE TABLE evolution_ticks (
    tick_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at         TEXT    NOT NULL,
    finished_at        TEXT,
    phase              TEXT    NOT NULL CHECK(phase IN (
        'baseline','claude_code','verdict','done','failed')),
    verdict            TEXT             CHECK(verdict IN (
        'ok','neutral','regression','regression_tests','regression_canary',
        'fail','flaky','give_up','skipped_budget') OR verdict IS NULL),
    batch_json         TEXT    NOT NULL,
    snaps_before_json  TEXT,
    snaps_after_json   TEXT,
    verdicts_json      TEXT,
    claude_session     TEXT,
    claude_cost_usd    REAL,
    claude_turns       INTEGER,
    commit_sha         TEXT,
    bundle_path        TEXT,
    error_summary      TEXT
);

CREATE INDEX idx_evolve_started ON evolution_ticks(started_at);
CREATE INDEX idx_evolve_verdict ON evolution_ticks(verdict);

CREATE TABLE evolution_skiplist (
    company_id     TEXT    PRIMARY KEY,
    reason         TEXT    NOT NULL CHECK(reason IN (
        'give_up','manual_blacklist','moex_overlap')),
    failure_count  INTEGER NOT NULL DEFAULT 0,
    last_tick_id   INTEGER REFERENCES evolution_ticks(tick_id),
    updated_at     TEXT    NOT NULL
);
