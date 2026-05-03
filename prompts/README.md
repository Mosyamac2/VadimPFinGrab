# Серия промптов для реализации e-disclosure extractor

Источник требований: `TZ_e-disclosure_extractor.md` в корне репозитория.

Промпты исполняются **строго последовательно** — каждый следующий опирается на артефакты предыдущего. После каждого промпта обязательно прогнать все указанные в нём тесты и убедиться, что они зелёные, прежде чем переходить дальше.

## Порядок выполнения

| # | Промпт | Что появляется в репозитории |
|---|---|---|
| 01 | [Каркас проекта](prompt_01_scaffolding.md) | структура каталогов, `pyproject.toml`, базовый CLI, structlog, pytest |
| 02 | [Конфигурация и секреты](prompt_02_configuration.md) | `config/*.yaml`, Pydantic-схемы, загрузка `.env` |
| 03 | [State-БД (SQLite)](prompt_03_state_db.md) | схема SQLite + миграции + репозитории |
| 04 | [HTTP-клиент и Discoverer](prompt_04_http_discoverer.md) | rate-limited httpx, парсинг карточек эмитентов, поиск новых публикаций |
| 05 | [Downloader и Unpacker](prompt_05_downloader_unpacker.md) | скачивание архивов с дедупликацией, распаковка RAR/ZIP |
| 06 | [PDF Classifier](prompt_06_pdf_classifier.md) | определение типа отчётности, признака скана, типа формы |
| 07 | [Text Extractor (нативный + OCR)](prompt_07_text_extractor.md) | pdfplumber/pymupdf + Tesseract OCR |
| 08 | [LLM-провайдер с fallback](prompt_08_llm_provider.md) | абстракция, Anthropic API (приоритет) + OpenRouter (fallback) |
| 09 | [Metric Extractor](prompt_09_metric_extractor.md) | извлечение финансовых показателей через LLM со строгой JSON-схемой |
| 10 | [Event Extractor](prompt_10_event_extractor.md) | структурирование сообщений о существенных фактах |
| 11 | [Validator (sanity checks)](prompt_11_validator.md) | балансовое уравнение, знаки, YoY, валюты, единицы |
| 12 | [Writer: SQLite-витрина + Excel](prompt_12_writer_excel.md) | запись в витрину `state.sqlite`, генерация `e-disclosure.xlsx` |
| 13 | [Репликация на Google Drive](prompt_13_google_drive.md) | OAuth2, update (а не create), опциональные снапшоты |
| 14 | [Оркестратор и CLI](prompt_14_orchestrator_cli.md) | DAG стадий, `update` / `run --full-reload`, идемпотентность |
| 15 | [Расписание и приёмочные тесты](prompt_15_scheduling_acceptance.md) | crontab/systemd timer, e2e-тесты, README с инструкцией по установке |

## Серия Patch 16–22 (адаптация под реальный e-disclosure.ru) — ✅ закрыта

После выкатки v1 на реальный сайт обнаружилось, что синтетические фикстуры, на которых написаны промпты 04, 06, 09, не совпадают с настоящей разметкой/структурой документов. План доработок собран в `PLAN_e-disclosure_parser_v2.md`; вся серия 16–22 реализована и помержена в master (см. историю коммитов с префиксами `feat(discoverer)`, `feat(classifier+extractor)`, `feat(metrics)`, `feat(tickers)`, `feat(metric-extractor)`, `docs(...)`).

**Принцип «мульти-эмитент»** проходит через всю серию: каждая поведенческая стадия (Discoverer/Classifier/Metric Extractor/Issuer-trim) тестируется на материалах **нескольких эмитентов из разных секторов**. На текущий момент задействованы:
- SBER (`id=3043`) — банк: 4 HTML-листинга, МСФО, РПБУ-гибрид, Issuer Report
- LKOH (`id=17`) — нефтегаз: HTML-листинг РСБУ (с 2009 г.), РСБУ Q1 2026 (чисто текст), Годовой отчёт 2025 (чисто текст)
- VTBR — банк не-Сбер: Годовой отчёт 2024 (чисто текст; уникальные термины «Чистые процентные доходы» во мн. ч.)

Любой код, неявно завязанный на разметку или термины одного конкретного эмитента, считается багом. Любой добавляемый синоним метрики обязан иметь подтверждение реальной фикстурой (см. `prompt_19`).

| # | Промпт | Что приземлилось | Breaking? |
|---|---|---|---|
| 16 | [Discoverer на реальном HTML](prompt_16_discoverer_real_html.md) | парсер `table.files-table`, обход 4 URL `files.aspx?id=X&type={2,3,4,5}` на тикер, fail-soft на отсутствующие типы; новый модуль `discoverer/period.py` | да: контракт `DiscoveredPublication` расширен (4 новых поля) |
| 17 | [Колонки период/тип в БД](prompt_17_publications_period_columns.md) | миграция `0007_publications_period.sql`: `report_type_code`, `reporting_period_year`, `reporting_period_type` + индекс; `PublicationsRepo.list_by_period` | нет (поля nullable) |
| 18 | [Постраничная классификация PDF](prompt_18_per_page_classification.md) | миграция `0008_documents_pages.sql` + `classify_pages()`; гибридный Text Extractor (нативный текст + OCR только для scan-страниц); `text_extractor_ocr_partial` лог только на гибридах | да: `is_machine_readable(...)` → `classify_pages(...)`, `app.classifier.min_text_chars_per_page` |
| 19 | [Профили банк / небанк](prompt_19_bank_nonbank_profiles.md) | `metrics.yaml → profiles: {bank, non_bank}`; `MetricSpec` с `synonyms`/`only_in_sources`/`aggregation_hint`; `TickerEntry.profile`; per-(profile, source) prompt+schema; Excel лист `tickers` с колонкой `profile`; удалён мёртвый `formula.py` | **да**: старый плоский `metrics.yaml` больше не загружается |
| 20 | [Top-50 tickers.yaml](prompt_20_top50_tickers.md) | scaffold `config/tickers.yaml` (51 эмитент, SBER+LKOH с реальными id); CLI `tools/find_e_disclosure_ids.py` (поиск id) и `tools/validate_tickers.py` (probe `OK/EMPTY/MISSING/ERROR` per type); `config/tickers.yaml.template` | нет |
| 21 | [Issuer Report как 3-й источник](prompt_21_issuer_report_source.md) | миграция `0009_issuer_reporting_standard.sql` (CHECK widening); `text_extractor/issuer_trim.py` (regex с alternation для трёх формулировок 1.4 + TOC-эвристика); 3-tier приоритет [IFRS, RSBU, ISSUER] в Metric Extractor; ISSUER-источник не отправляется как PDF, текст обрезается до 1.4; удалён ISSUER→RSBU shim из Patch 19 | нет |
| 22 | [Документация и косметика](prompt_22_cosmetic_docs.md) | README/USER_GUIDE под профили + sources tickers.yaml workflow; `app.yaml` с `min_text_chars_per_page` + `issuer_trim_max_chars`; `metrics.yaml.template`; `unrar` → опциональный | нет |
| 23 | [Playwright HTTP-бэкенд (ServicePipe / JA3)](prompt_23_playwright_backend.md) | `PlaywrightEDisclosureClient` (наследник `EDisclosureClient`); `app.discoverer.http_backend: httpx \| playwright`; опциональная зависимость `pip install '.[playwright]'`; обходит JA3-fingerprint ServicePipe на live-сайте | нет (дефолт `httpx`) |

**Фактический порядок исполнения** (с метками коммитов): `17 + 16` (один коммит — миграция нужна до парсера) → `18` → `19` → `20` → `21` → `22` → `23`. Patch 23 добавлен после `22` потому, что только на боевом запуске стало видно: ServicePipe валидирует JA3-fingerprint, и cookies-обходный путь нестабилен — для cron-прогона нужен Playwright.

## Серия Patch 24–28 (production-hardening после live-прогона) — ✅ закрыта без отдельных промптов

После Patch 23 на боевом VPS вылезли мелкие, но дорогостоящие баги в стадиях ниже Discoverer/Downloader; они правились ad-hoc без отдельных prompt-файлов. Полный реестр — в [`README.md` § «v3 — production-hardening»](../README.md#v3--production-hardening-после-live-прогонов-patch-2328--завершено) и в логе коммитов.

| # | Что приземлилось | Breaking? |
|---|---|---|
| 24 | HTTPS-прокси для Anthropic + Google Drive (`httplib2.proxy_info_from_environment`); `NO_PROXY=e-disclosure.ru` оставляет портал в обход прокси | нет |
| 25 | Classifier: deterministic `type_code → reporting_standard` маппинг даже для scan-only PDF (раньше попадали в `OTHER` и игнорировались Metric Extractor'ом) | нет |
| 26 | Metric Extractor: dedup по `(date, period_type, std, metric_name)` — LLM иногда отдаёт две `extractions`-записи за один период | нет |
| 27 | Drop comparative-period rows (фильтр на `period.period_type == pub.reporting_period_type`) + `INSERT OR REPLACE` в `metrics_repo` для cross-publication коллизий | нет |
| 28 | Anthropic prompt-caching observability (`cache_read_input_tokens`/`cache_creation_input_tokens` + `cache_hit_ratio`); `llm.primary.cache_ttl: 1h` default — кэш не испаряется в середине `edx run --full-reload` | нет |

## Серия Patch 29–34 (улучшение coverage на сканированных формах) — 🟡 in flight

После live-прогона `run_id=13` (38 written / 2 incomplete / 6 skipped) обнаружилось, что Anthropic native-PDF-input не вытягивает цифры из русских РСБУ-форм с тонкой grid-таблицей и подписью главбуха — независимо от того, гибридный документ или чисто текстовый. Пострадало минимум 6 публикаций в одном прогоне (CHMF FY 2025 / VTBR Q1 2026 / SBER 9M+12M 2025 / VTBR FY 2025 / VTBR IFRS H1 2025). Корневой анализ — в `PIPELINE_LOGIC.md` §5 + history-блоке за 2026-05-02.

Серия Patch 29–34 разбивает решение на дискретные задачи с чётким приоритетом. Patches 29 + 30 + 32 — обязательная база (P0); 31 — улучшение Tesseract (P1); 33 + 34 — opt-in fallback'ы под флагом, реализуются только если P0–P1 не закрыли всё.

| # | Промпт | Что приземлится | Pri | Breaking? |
|---|---|---|---|---|
| 29 | [Hybrid-PDF и RSBU через text-path](prompt_29_hybrid_pdf_text_path.md) | `MetricExtractorService.send_pdf` дополняется `scan_ratio_threshold` + `pdf_input_standards`; RSBU и hybrid-документы шлются как text, не PDF | P0 | нет (default IFRS остаётся PDF-путь) |
| 30 | [Якорь начала балансовой формы](prompt_30_balance_anchor_trim.md) | `text_extractor/balance_anchor.py` с `extract_balance_sheet_onwards`; для RSBU обрезается аудиторское заключение до якоря «БУХГАЛТЕРСКИЙ БАЛАНС» / «Форма по ОКУД 0710001» | P0 | нет (при отсутствии якоря fail-soft на полный текст) |
| 31 | [Tesseract DPI 400 + PSM 6 + retry](prompt_31_tesseract_quality.md) | default DPI 300→400, PSM 3→6, per-page retry с PSM 4 при низком digit-ratio | P1 | нет (knobs, дефолты улучшаются) |
| 32 | [Discoverer: парсинг reporting_period](prompt_32_period_parser_extensions.md) | новые regex-паттерны (короткое «за 2025 год», «1 квартал 2026 года» с пробелами, fallback на `<a title>`) → Patch 27 (drop comparative) корректно срабатывает на FY-RSBU | P1 | нет (аддитивные паттерны) |
| 33 | [Vision-fallback при низком coverage](prompt_33_vision_fallback.md) | `LLMRequest.pdf_page_indices`; ретрай через Anthropic vision только на scan-страницах при `coverage_ratio < threshold`; opt-in через `vision_fallback_enabled: false` | P2 | нет (default off) |
| 34 | [Full vision path opt-in per-ticker](prompt_34_full_vision_per_ticker.md) | `tickers.yaml.use_vision_extraction: true` + `LLMRequest.pdf_page_images`; image-content-blocks вместо PDF; глобальный kill switch | P3 | нет (opt-in per-ticker) |

**Зависимости внутри серии:** Patch 30/31/32 — независимы, можно параллельно. Patch 29 — обязательная предпосылка для Patch 30/33/34 (без неё RSBU вообще не доходит до text-path). Patch 33 — должен идти после 30+31, иначе будет fallback'иться даже на дешёвых случаях. Patch 34 — после 33; рассматривается только если оставшиеся проблемные эмитенты после P0–P2 ≥ 1.

## Серия Patch 35–37 (long-tail после расширения тикерного списка) — 🟡 in flight

После масштабирования с 5 до 6 тикеров (добавлены IZNM, PHOR, SLGD) run_id=14 показал три остаточных класса проблем: (1) issuer_trim возвращает 181-байт slice для малых эмитентов с TOC-only ссылкой на раздел 1.4; (2) часть эмитентов выкладывают Issuer Report как `.rtf`, не PDF; (3) одна банковская РСБУ-публикация (Сбербанк МСА 805 Q1 2025, 11-page scan-only) даёт 40% coverage после Tesseract @ 400 DPI — кейс ровно для vision-fallback.

| # | Промпт | Что приземлится | Pri | Breaking? |
|---|---|---|---|---|
| 35 | [Issuer-trim min-length validation](prompt_35_issuer_trim_min_length.md) | `extract_section_1_4` отбрасывает slice'ы < 500 chars и пары matches на расстоянии < 3000 chars (TOC-only); fall-back на полный текст | P0 | нет (доп. опциональные параметры) |
| 36 | [RTF support](prompt_36_rtf_support.md) | `striprtf` зависимость; `extract_text_from_rtf`; Classifier и Text Extractor поддерживают `.rtf` наравне с PDF | P0 | нет (новый формат, не трогает существующий) |
| 37 | [Enable vision_fallback by default](prompt_37_enable_vision_fallback.md) | `vision_fallback_enabled: true` в `app.yaml`, max_pages 12→8 | P1 | нет (флаг + cap; код не меняется) |

**Зависимости внутри серии:** Patch 35 и 36 независимы. Patch 37 — просто включение Patch 33-инфраструктуры; зависит от 33, который уже на master. Все три независимы между собой и могут идти в любом порядке.

**Зависимости от внешнего сайта:** к Patch 23 эта строка уже не актуальна — Playwright-бэкенд штатно обходит ServicePipe-challenge. Для тестов всё равно используются фикстуры (network-free, быстрые); live-проверка Playwright-пути остаётся ручной (см. DoD в `prompt_23`).

## Серия Patch 38–46 (Self-Evolution Loop) — 🟡 запланирована

Цель серии — добавить в проект функциональность **самоэволюции**: каждые 5 минут демон берёт батч из 3 компаний из `e-disclosure-companies.csv`, прогоняет на них пайплайн, на провале запускает headless Claude Code (с обязательным чтением и обновлением `evolution/MEMORY.md`) и автоматически мерджит зелёный фикс в `master`. Полный план — в [`PLAN_self_evolution.md`](../PLAN_self_evolution.md).

| # | Промпт | Что приземлится | Pri | Breaking? |
|---|---|---|---|---|
| 38 | [DB migration + EvolutionRepo + MEMORY.md template](prompt_38_evolution_db.md) | миграция `0010_evolution.sql`, модели/репозиторий, шаблон `evolution/MEMORY.md`, `.gitignore`-допы | P0 | нет (additive) |
| 39 | [CSV loader + Picker + Synth](prompt_39_evolution_csv_picker_synth.md) | `src/edx/evolve/{csv_loader,picker,synth}.py`; батч=3, MOEX-skip, cooldown; материализация `config-evolve/` | P0 | нет |
| 40 | [Runner + Snapshot + Verdict + первая версия CLI tick](prompt_40_evolution_runner_snapshot_verdict.md) | `evolve/{runner,snapshot,verdict,tick}.py`, subparser `edx evolve tick` без агента (dry-run) | P0 | нет |
| 41 | [Diagnostic Bundle + Failure Taxonomy + Canaries](prompt_41_evolution_bundle_taxonomy_canaries.md) | `evolve/{bundle,taxonomy,canaries}.py`; на FAIL bundle полностью собран | P0 | нет |
| 42 | [Memory module + headless Claude Code runner](prompt_42_evolution_memory_and_claude_code.md) | `evolve/{memory,claude_runner}.py`; `.claude/settings.evolve.json`; slash `edx-evolve-fix.md`; feature flag | P0 | нет (агент по умолчанию off) |
| 43 | [Verdict gate + git auto-merge в master + расширенный CLI](prompt_43_evolution_verdict_gate_git.md) | `evolve/git_ops.py`; полный gate (tests + canaries + batch + memory); `evolve status/replay/report/reset/memory show/verify/compact/canary capture` | P0 | нет (gate включается только при `EDX_EVOLVE_AGENT_ENABLED=1`) |
| 44 | [Deploy: Claude Code install, systemd, env шаблон](prompt_44_evolution_deploy_remote_server.md) | `deploy/install_claude_code.sh`; `deploy/systemd/edx-evolve.{service,timer}`; `deploy/env.evolve.example`; раздел README | P1 | нет |
| 45 | [Pilot — dry-run наблюдение + калибровка](prompt_45_evolution_pilot_dryrun.md) | процедурный патч: 30 dry-run + 5 agent-on тиков, taxonomy/budget/timeout tweaks | P1 | нет |
| 46 | [Production rollout + monitoring + runbook](prompt_46_evolution_production_rollout.md) | `EDX_EVOLVE_AGENT_ENABLED=1` на проде; `edx evolve cleanup`; `slo-smoke` make-target; final operator runbook | P1 | нет |

**Зависимости внутри серии:** строго последовательны. Patch 38–43 безопасны к merge даже без планируемого Patch 46 (агент off по умолчанию). Patch 44 — операционный (только deploy-файлы и docs). Patch 45 — pilot, не код. Patch 46 — финальное включение + cleanup-инструменты.

**Решения, согласованные с оператором (2026-05-03):**
- auto-merge в `master` после прохождения 4-уровневого gate (без PR-review);
- дневной бюджет $25, per-tick $2;
- профиль `bank|non_bank` берётся из колонки `type` в `e-disclosure-companies.csv`;
- размер батча — 3 компании на тик (для обобщения и anti-regression внутри батча);
- долгосрочная память `evolution/MEMORY.md` обязательна — Claude Code должен её прочесть в STEP 0 и обновить в STEP 4 каждого тика, иначе тик считается failed.

## Соглашения

- Каждый промпт приводит ссылку на разделы ТЗ, к которым он относится.
- В каждом промпте есть блок **«Тесты, которые должны проходить»** — это gating-критерий перехода к следующему этапу.
- Не объединять промпты и не идти вперёд при незелёных тестах.
- Рабочее дерево после каждого промпта должно оставаться запускаемым (`pytest -q` зелёный).

## Параметры запуска промптов

Перед стартом каждого промпта:

```
git checkout -b step-NN-<short-name>
```

После прохождения тестов:

```
git add -A && git commit -m "step NN: <short summary>"
```

Это даст возможность откатиться к любому этапу без потери работы.
