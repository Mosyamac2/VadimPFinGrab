# e-disclosure extractor

ETL pipeline that scrapes financial reporting from
[e-disclosure.ru](https://www.e-disclosure.ru/), extracts metrics from PDF
reports via an LLM, and replicates the result to Google Drive as an Excel mart.

The full requirement spec is in
[`TZ_e-disclosure_extractor.md`](TZ_e-disclosure_extractor.md). The
implementation is decomposed into 15 sequential prompts in
[`prompts/`](prompts/README.md).

## Status

- ✅ Prompt 01 — project scaffolding
- ✅ Prompt 02 — configuration & secrets
- ✅ Prompt 03 — SQLite state DB + repositories
- ✅ Prompt 04 — HTTP client + Discoverer stage
- ✅ Prompt 05 — Downloader + Unpacker stages
- ✅ Prompt 06 — PDF Classifier stage
- ✅ Prompt 07 — Text Extractor (native + OCR)
- ✅ Prompt 08 — LLM provider chain (Anthropic + OpenRouter fallback)
- ✅ Prompt 09 — Metric Extractor (LLM)
- ⬜ Prompts 10–15 — pending

## System packages

The pipeline needs a few system-level tools beyond Python wheels:

```bash
# Required to extract RAR archives produced by some issuers' submissions.
sudo apt install unrar
# Required to OCR scanned PDFs (Tesseract + Russian/English language packs +
# poppler-utils for the pdf2image bridge).
sudo apt install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils
```

Without `unrar`, RAR-archived publications are skipped with a warning; ZIP
archives still work. Without Tesseract / poppler, scanned PDFs cannot be OCR'd
and the publication is marked failed at the Text Extractor stage; native
machine-readable PDFs still process.

## Quick start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
make install
cp .env.example .env             # then fill the required keys
edx config check                 # validates all YAML and prints loaded values
edx update                       # runs an incremental pass (stub for now)
```

CLI shape:

| Command | Purpose |
|---|---|
| `edx update` | Incremental run (the "refresh" button) — default cron mode. |
| `edx run --full-reload` | Re-process the last 3 years of publications. |
| `edx config check [--format yaml\|json]` | Validate config and print all values with secrets masked. |
| `edx --config-dir DIR ...` | Override the config directory (default `./config`). |

## Configuration

All settings live as YAML files under `config/` and are validated at startup
by Pydantic. Re-read on every invocation — no caching. Secrets come from
`.env` via `pydantic-settings`.

| File | Schema | Purpose |
|---|---|---|
| `config/app.yaml` | `AppConfig` | Filesystem paths (ТЗ §10.1), cron schedule, default run mode, backfill depth, optional contact email used in the scraper User-Agent. |
| `config/tickers.yaml` | `TickersConfig` | Issuer registry: MOEX ticker → e-disclosure ID, plus optional INN/OGRN and a per-issuer `priority_override` (`IFRS`/`RSBU`). |
| `config/metrics.yaml` | `MetricsConfig` | Canonical metric names + per-standard synonyms + units/currency, plus optional derivation `formula`. Includes a top-level `reporting_priority` (`["IFRS","RSBU"]`). |
| `config/event_types.yaml` | `EventTypesConfig` | Material-event taxonomy (codes + display names + aliases). Must contain a `code: other` fallback entry. |
| `config/llm.yaml` | `LLMConfig` | Primary Anthropic provider + OpenRouter fallback, shared `max_tokens` / `temperature` / `request_timeout_s` / `max_retries` / `concurrency`. |
| `config/ocr.yaml` | `OCRConfig` | OCR engine choice (`tesseract` / `yandex_vision` / `google_vision`), Tesseract languages and DPI, options for cloud engines. |
| `.env` | `Secrets` | API keys and OAuth tokens. Keys: `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`, `YANDEX_VISION_OCR_KEY`. |

### Adding things

- **New issuer** → append to `tickers.yaml`. No code changes.
- **New metric** → append to `metrics.yaml` (with synonyms in both standards).
  No code changes.
- **New event type** → append to `event_types.yaml`. No code changes.

### Validation behaviour

Any extra (unknown) field, type mismatch, or invalid `reporting_priority`
(non-`IFRS`/`RSBU`) value triggers a `ValidationError`. The CLI exits with code
`2` and emits a structured `config_load_failed` log line carrying the offending
file path and dotted field name.

## Development

```bash
make lint        # ruff
make typecheck   # mypy strict
make test        # pytest
```

See [`prompts/README.md`](prompts/README.md) for the implementation roadmap.
