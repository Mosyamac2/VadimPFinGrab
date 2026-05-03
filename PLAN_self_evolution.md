# PLAN: Self-Evolution Loop

> Версия документа: 2026-05-03 (rev. 2 — после согласования с оператором).
> План реализации функциональности «самоэволюции» над текущим пайплайном
> `edx`. Серия патчей: **Patch 38–46**.

> **Согласованные оператором решения (2026-05-03):**
> 1. Авто-merge в `master` после зелёного gate. Авто-эволюция должна быть
>    автономной end-to-end; ручной PR-review не требуется.
> 2. Дневной бюджет — $25/день (Anthropic API + Claude Code суммарно).
>    Per-tick cap — $2.
> 3. Профиль `bank | non_bank` берётся из колонки `type` файла
>    `e-disclosure-companies.csv` напрямую — без эвристики.
> 4. Каждый тик работает с **батчем из 3 компаний**, а не с одной.
>    Дополнительно поддерживается **долгосрочная память**
>    (`evolution/MEMORY.md`), которую агент обязан прочитать ДО изменений
>    и обновить ПОСЛЕ успешного патча — для накопления обобщающей
>    способности и анти-регрессий.

---

## 0. TL;DR

Каждые 5 минут демон `edx-evolve` берёт **батч из 3 компаний** из
`e-disclosure-companies.csv` (колонка `type` указывает профиль), записывает
их в `config-evolve/tickers.yaml`, прогоняет на них текущий пайплайн
`edx update --ticker EDX… --ticker EDX… --ticker EDX…` в изолированной
конфигурации, делает snapshot до/после по каждой компании. Если все три
успешны — данные мерджатся в общий `e-disclosure-new.xlsx`, тик закрыт.

Если есть провалы — собирается **агрегированный Diagnostic Bundle** (логи +
срезы state.sqlite по каждой компании батча + автоклассификация ошибок) и
запускается **headless Claude Code** (`claude -p`) с двумя обязательными
шагами: `(a)` прочесть `evolution/MEMORY.md` (выжимка прошлого опыта и
анти-регрессионные заметки), `(b)` после удачного патча — обновить
`MEMORY.md` новой записью.

Если новый патч проходит **gate** (все тесты зелёные + батч
улучшил/не ухудшил покрытие + 3 канарейки SBER/LKOH/IZNM не сломались) —
demon делает fast-forward merge в `master` и `git push`. Иначе — `git reset
--hard` на временной ветке, патч выкидывается, компании из батча уходят в
skiplist.

Цель — **накопительное** обобщение: после ~N тиков пайплайн справляется с
типовыми кейсами без участия оператора, а MEMORY.md превращается в живую
библиотеку решённых failure-классов и анти-паттернов.

---

## 1. Цели и не-цели

### 1.1 Цели (must)

1. Обработать ≥100 компаний из `e-disclosure-companies.csv` существующим
   пайплайном без ручного вмешательства, с автоматическим расширением
   функциональности под новые кейсы.
2. Каждый отдельный тик — детерминированный, ограниченный по времени и
   стоимости (LLM-кредиты Anthropic + кредиты Claude Code).
3. Любое автоулучшение проходит через **зелёный pytest + ruff + mypy** до
   коммита; иначе откат.
4. Полный аудитный след: для каждой попытки видно, какая компания, какая
   ошибка, какой Claude Code session_id, какой коммит, какой бенчмарк
   до/после.
5. Никаких авто-правок в `.env`, ключей, deploy-конфигов, force-push'ей
   или изменений на master в обход PR.
6. Работает на удалённом Linux-сервере (тот же VPS, где сейчас крутится
   `edx-update.timer`); процесс независим от ноутбука оператора.

### 1.2 Не-цели (won't)

- Не переписываем core-пайплайн «с нуля» — Claude Code работает только в
  режиме точечных патчей.
- Не используем Claude Code как runtime для ETL — он остаётся meta-слоем
  улучшений, а сам пайплайн `edx` запускается обычным `python`.
- Не покрываем events/новости — фокус на `metrics` (как явно просил
  пользователь, «отчётности разных типов за последний год»).
- Не масштабируемся за пределы одной машины (мульти-нода / Celery / k8s
  выходят за scope).
- Не строим UI поверх — оператор работает через `git log`, `edx evolve
  status` и Excel.

---

## 2. Глоссарий

| Термин | Значение |
|---|---|
| **Tick** | Одна итерация цикла самоэволюции. Тик = (выбор компании) → (run pipeline) → (success | failure-and-fix). |
| **Target company** | Запись из `e-disclosure-companies.csv` (`id`, `name`), для которой выполняется текущий тик. |
| **Synthetic ticker** | Тикер, синтезированный из id, чтобы влезть в существующую модель данных (формат: `EDX{id}`, например `EDX1210`). Реальные MOEX-тикеры из `config/tickers.yaml` сохраняются как есть. |
| **Diagnostic Bundle** | Артефакт-папка `evolution/runs/{tick_id}/`, в которую складываются логи, срез БД, prompt-входы, prompt-ответы, диффы. |
| **Evolution Patch** | Коммит, созданный Claude Code в результате тика. Соглашение: префикс `evolve(N):` где N — `tick_id`. |
| **Coridor** | Набор разрешённых файлов, тулзов и операций для Claude Code. Всё снаружи коридора блокируется на уровне sandbox-permissions. |
| **Improvement Verdict** | OK / NEUTRAL / REGRESSION / FLAKY — результат сравнения бенчмарка до и после патча. Только OK/NEUTRAL пушатся. |

---

## 3. Архитектура высокого уровня

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          edx-evolve.timer (5 min)                        │
└───────────────────────────────┬──────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  edx evolve tick                         (новый CLI-subcommand)          │
│                                                                          │
│  1. Picker → выбирает БАТЧ из 3 компаний из e-disclosure-companies.csv:  │
│       a) приоритет: never_attempted > failed_recoverable > regression    │
│       b) skiplist: company_id с failure_count ≥ 3 пропускается           │
│       c) повторно ОК-компании не берём в течение N=7 дней                │
│                                                                          │
│  2. Synth → пишет config-evolve/tickers.yaml с тремя записями            │
│       (ticker = "EDX{id}", profile из колонки CSV.type) +                │
│       config-evolve/app.yaml (override mode.backfill_years=1)            │
│                                                                          │
│  3. Baseline run → edx update --ticker EDX… --ticker EDX… --ticker EDX…  │
│       --config-dir config-evolve. Snapshot до/после ПО КАЖДОЙ компании.  │
│                                                                          │
│  4. Per-company verdicts: OK | NEUTRAL | REGRESSION | FAIL               │
│                                                                          │
│  ─── Все 3 в OK/NEUTRAL ───┐                                             │
│                            ▼                                             │
│           Merge в общий output/e-disclosure-new.xlsx; tick done.         │
│                                                                          │
│  ─── Хотя бы 1 FAIL/REGRESSION ───┐                                      │
│                                   ▼                                      │
│  5. Diagnostic Bundle — агрегированный по 3 компаниям:                   │
│       evolution/runs/{tick_id}/                                          │
│         ├ batch.json                  # 3 entries                        │
│         ├ snap_before.json, snap_after.json  (per-company)               │
│         ├ pipeline.log + pipeline.log.errors                             │
│         ├ state-slice.sql            (filter по 3 ticker'ам)             │
│         ├ failure_taxonomy.json     (per-company классификация)          │
│         ├ memory_snapshot.md        (копия evolution/MEMORY.md на момент)│
│         └ prompt.md                  (системный для Claude Code)          │
│                                                                          │
│  6. Headless Claude Code:  claude -p "/edx-evolve-fix {tick_id}"         │
│       --output-format stream-json --max-turns 25                         │
│       --permission-mode acceptEdits                                      │
│       Slash-command обязывает агента:                                    │
│         (a) ПРОЧЕСТЬ evolution/MEMORY.md и applied-patches               │
│         (b) сделать минимальный патч                                     │
│         (c) прогнать make test/lint/typecheck                            │
│         (d) перезапустить пайплайн на ВСЕХ 3 тикерах + 3 канарейках      │
│             (SBER/LKOH/IZNM) для anti-regression                         │
│         (e) обновить MEMORY.md новой записью с root cause + anti-pattern │
│                                                                          │
│  7. Verdict gate:                                                        │
│       - tests зелёные                                                    │
│       - канарейки не ухудшились                                          │
│       - батч улучшен (≥1 FAIL стал OK; ≥0 OK НЕ стал FAIL)              │
│       - MEMORY.md обновлён                                               │
│       Если ВСЁ OK → git fast-forward в master + git push origin master   │
│       Иначе      → git reset --hard на ветке tick; bump failure_count    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Контракт «CSV → пайплайн»

**Проблема:** существующий `config/tickers.yaml` оперирует моделью «тикер
MOEX → e_disclosure_id», а CSV даёт `(id, name, type)`. Несовместимо в
лоб: пайплайн делает `INSERT OR REPLACE INTO tickers (ticker, …)`, и без
ticker мы не уложим запись.

**Решение:** ввести **synthetic ticker** формата `EDX{id}`. Профиль
читается напрямую из колонки CSV — никакой эвристики:

```python
# src/edx/evolve/csv_loader.py
@dataclass(frozen=True, slots=True)
class CompanyRow:
    company_id: str          # "1210"
    name: str                # 'Банк ВТБ (ПАО)'
    type: Literal["bank", "non_bank"]   # из CSV колонки

    @property
    def synthetic_ticker(self) -> str:
        return f"EDX{self.company_id}"

def load_companies(path: Path = Path("e-disclosure-companies.csv")) -> list[CompanyRow]:
    rows = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(CompanyRow(
                company_id=r["id"].strip(),
                name=r["name"].strip(),
                type=r["type"].strip().lower(),  # "bank" | "non_bank"
            ))
    return rows
```

Это даёт идемпотентный ticker_id, не пересекается с реальными MOEX
тикерами (3-5 латинских букв), и без миграции ложится в текущую модель
state.sqlite.

**Батч из 3 компаний** на тик: за один прогон Synth кладёт ровно три
записи в `config-evolve/tickers.yaml`. Pipeline проходит для каждой по
тем же стадиям; per-company snapshot позволяет различать кейсы внутри
батча. Преимущество батча для self-evolve:

- Claude Code видит ОДНОВРЕМЕННО 3 разных провала и может предложить
  обобщённый фикс, а не точечный.
- Anti-regression проверка: фикс должен починить ≥1 провального тикера
  батча, не сломав остальные два.

**Изоляция:** evolve-демон работает с **отдельным config-dir**
`config-evolve/`:

```
config-evolve/
  ├ tickers.yaml         # переписывается каждый тик: 3 батч-компании
  ├ app.yaml             # копия config/app.yaml + override mode.backfill_years=1
  ├ metrics.yaml         # симлинк на config/metrics.yaml
  ├ event_types.yaml     # симлинк
  ├ ocr.yaml             # симлинк
  └ llm.yaml             # симлинк
```

Симлинки гарантируют, что улучшения метрик в config/metrics.yaml
автоматически подхватываются на следующем тике без копирования.

**Skiplist пересекающихся MOEX-тикеров:** компании из CSV, чьи
`e_disclosure_id` уже замаплены в основном `config/tickers.yaml`
(SBER=3043, LKOH=17, …), пропускаются Picker'ом — они обрабатываются
обычным `edx update` и попадают в xlsx через стандартный путь.

**Атомарность XLSX-витрины:**

- Evolve-прогон пишет в `output/e-disclosure-evolve.xlsx` (отдельный файл).
- На успехе данные мерджатся (UPSERT по `(ticker, reporting_date,
  period_type, reporting_standard, metric_name)`) в общий
  `e-disclosure-new.xlsx`. Если общий файл занят (`.~lock.xlsx`), мердж
  откладывается до следующего тика; данные не теряются — они в
  state.sqlite.

---

## 5. Расширение state.sqlite

Новая миграция `0010_evolution.sql`:

```sql
CREATE TABLE evolution_ticks (
    tick_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT    NOT NULL,
    finished_at      TEXT,
    company_id       TEXT    NOT NULL,
    company_name     TEXT    NOT NULL,
    ticker           TEXT    NOT NULL,        -- EDX{id}
    phase            TEXT    NOT NULL,        -- baseline | claude_code | verdict | done
    verdict          TEXT,                    -- ok | neutral | regression | fail | flaky | give_up
    metrics_before   TEXT,                    -- JSON: {ticker_metrics_count, qa_issues, ...}
    metrics_after    TEXT,                    -- JSON
    claude_session   TEXT,                    -- claude code session_id
    claude_cost_usd  REAL,
    claude_turns     INTEGER,
    commit_sha       TEXT,                    -- если запушили
    bundle_path      TEXT,                    -- evolution/runs/{tick_id}/
    error_summary    TEXT,
    UNIQUE(tick_id)
);

CREATE INDEX idx_evolve_company ON evolution_ticks(company_id, started_at);
CREATE INDEX idx_evolve_verdict ON evolution_ticks(verdict);

CREATE TABLE evolution_skiplist (
    company_id     TEXT PRIMARY KEY,
    reason         TEXT NOT NULL,             -- give_up | manual_blacklist
    failure_count  INTEGER NOT NULL DEFAULT 0,
    last_tick_id   INTEGER REFERENCES evolution_ticks(tick_id),
    updated_at     TEXT NOT NULL
);
```

**Skiplist** — компании, на которых 3 тика подряд получили
REGRESSION/FAIL: помечаются `give_up` и Picker их пропускает, пока
оператор не сбросит вручную (`edx evolve reset --company-id 1210`).

---

## 6. Цикл «Self-Evolve» — детальный алгоритм одного тика

```python
def tick():
    # 6.1 Daily budget gate
    if today_spent_usd() >= DAILY_BUDGET_USD:
        log("evolve_skipped_daily_budget"); return

    # 6.2 Picker — БАТЧ из 3 компаний
    batch = pick_next_batch(
        csv_path="e-disclosure-companies.csv",
        skiplist=load_skiplist(),
        moex_overlap=load_moex_ids("config/tickers.yaml"),
        cooldown_days=7,
        size=3,
    )
    if len(batch) < 3:
        log("evolve_no_candidates", picked=len(batch)); return

    tick_id = create_evolution_tick(batch, phase="baseline")
    bundle_dir = Path(f"evolution/runs/{tick_id}")
    bundle_dir.mkdir(parents=True)

    # 6.3 Synth — пишем 3 записи в config-evolve/tickers.yaml
    write_evolve_config(batch, target="config-evolve/")

    # 6.4 Baseline snapshot — per-company
    snaps_before = {c.synthetic_ticker: snapshot_state(c.synthetic_ticker)
                    for c in batch}
    save(bundle_dir / "snap_before.json", snaps_before)

    # 6.5 Pipeline run на всём батче
    args = [".venv/bin/edx", "update", "--config-dir", "config-evolve"]
    for c in batch:
        args += ["--ticker", c.synthetic_ticker]
    res = subprocess.run(
        args,
        env={**os.environ, "EDX_LOG_FILE": str(bundle_dir / "pipeline.log")},
        timeout=30 * 60,
        capture_output=True,
    )
    snaps_after = {c.synthetic_ticker: snapshot_state(c.synthetic_ticker)
                   for c in batch}

    # 6.6 Per-company verdicts
    verdicts = {t: compute_verdict(snaps_before[t], snaps_after[t])
                for t in snaps_before}
    failing = [t for t, v in verdicts.items() if v in ("fail", "regression")]

    # 6.7 Success short-circuit
    if not failing:
        merge_to_xlsx([c.synthetic_ticker for c in batch])
        finalize_tick(tick_id, "ok", verdicts, snaps_after)
        return

    # 6.8 Diagnostic Bundle (агрегированный по 3 компаниям)
    build_diagnostic_bundle(
        bundle_dir=bundle_dir,
        batch=batch,
        snaps_before=snaps_before,
        snaps_after=snaps_after,
        verdicts=verdicts,
        log_path=bundle_dir / "pipeline.log",
        state_db="data/state.sqlite",
        memory_path="evolution/MEMORY.md",      # КОПИЯ кладётся в bundle
    )
    update_phase(tick_id, "claude_code")

    # 6.9 Создаём временную ветку для патча
    branch = f"evolve/tick-{tick_id}"
    git_checkout_new_branch(branch, base="master")

    # 6.10 Headless Claude Code (см. §8)
    claude_res = run_claude_code(
        bundle_dir=bundle_dir,
        tick_id=tick_id,
        budget_usd=TICK_BUDGET_USD,            # 2.0
        max_turns=25,
    )

    if not claude_res.modified_files:
        git_abandon_branch(branch)
        finalize_tick(tick_id, "fail", reason="claude_no_changes")
        bump_skiplist([c for c in batch if verdicts[c.synthetic_ticker] != "ok"])
        return

    # 6.11 Validation gate
    if not run_make_target("test"):
        git_abandon_branch(branch)
        finalize_tick(tick_id, "regression_tests", reason="tests_red")
        bump_skiplist([c for c in batch if verdicts[c.synthetic_ticker] != "ok"])
        return

    # 6.12 Re-run батча после патча
    res2 = run_pipeline_again([c.synthetic_ticker for c in batch])
    snaps_retry = {c.synthetic_ticker: snapshot_state(c.synthetic_ticker)
                   for c in batch}
    verdicts_retry = {t: compute_verdict(snaps_before[t], snaps_retry[t])
                      for t in snaps_retry}

    # 6.13 Канарейки — НЕ должны деградировать
    canary_ok = check_canaries(
        tickers=("SBER", "LKOH", "IZNM"),
        baseline=load_canary_baseline(),       # cached snapshot
    )
    if not canary_ok:
        git_abandon_branch(branch)
        finalize_tick(tick_id, "regression_canary", reason="canary_failed")
        bump_skiplist([c for c in batch if verdicts[c.synthetic_ticker] != "ok"])
        return

    # 6.14 Успех? минимум один failing стал OK, и ни один OK не стал FAIL
    improved = any(verdicts[t] in ("fail", "regression")
                   and verdicts_retry[t] == "ok"
                   for t in verdicts)
    not_regressed = all(verdicts_retry[t] != "regression" for t in verdicts)
    memory_updated = (Path("evolution/MEMORY.md").read_text() !=
                      bundle_dir.joinpath("memory_snapshot.md").read_text())

    if improved and not_regressed and memory_updated:
        sha = git_commit_and_merge_to_master(
            branch=branch,
            message=f"evolve({tick_id}): batch [{','.join(c.synthetic_ticker for c in batch)}]\n"
                    f"\nfailure_classes: {[v.taxonomy for v in failing_classes]}\n"
                    f"Claude Code session: {claude_res.session_id}\n"
                    f"\nUpdated evolution/MEMORY.md.",
            push=True,
        )
        merge_to_xlsx([c.synthetic_ticker for c in batch])
        finalize_tick(tick_id, "ok", verdicts_retry,
                      commit_sha=sha, claude_cost=claude_res.cost_usd)
    else:
        git_abandon_branch(branch)
        reasons = []
        if not improved: reasons.append("no_improvement")
        if not not_regressed: reasons.append("regression")
        if not memory_updated: reasons.append("memory_not_updated")
        finalize_tick(tick_id, "fail", reason=",".join(reasons))
        bump_skiplist([c for c in batch if verdicts_retry[c.synthetic_ticker] != "ok"])
```

**Ключевые инварианты:**

- `run_pipeline_again` использует **кэш LLM-ответов** (`data/processed/_llm_cache/`),
  поэтому re-run одного тикера дешёв. Кэш инвалидируется только когда
  изменился промпт или схема.
- `git_abandon_branch` ограничен: оперирует ТОЛЬКО ветками вида
  `evolve/tick-N`, никогда не трогает `master`. Реализован как
  `git checkout master && git branch -D evolve/tick-N`.
- `git_commit_and_merge_to_master` использует **fast-forward only** —
  если master ушёл вперёд (что в норме не должно случаться, тики
  сериализованы flock'ом), merge падает и мы откатываемся.
- Все таймауты: pipeline 30min (3 компании), Claude Code 30min, total
  tick 75min. Интервал таймера 5min × запас 75min — нужен flock на
  `/tmp/edx-evolve.lock`, чтобы тики не накладывались.
- Memory check: тик считается успешным только если `evolution/MEMORY.md`
  изменился относительно snapshot'а из bundle. Это ловит случай, когда
  Claude Code «забыл» обновить память.

---

## 7. Failure Analyzer и Diagnostic Bundle

Bundle — это input для Claude Code. Структура:

```
evolution/runs/{tick_id}/
├── company.json              # id, name, ticker, profile
├── snap_before.json          # state-counts ДО
├── snap_after.json           # state-counts ПОСЛЕ
├── pipeline.log              # полный лог тика (5–20MB)
├── pipeline.log.errors       # фильтр grep level=error/warning (≤500KB)
├── state-slice.sql           # SELECT … FROM publications, documents,
│                             # qa_issues, metrics WHERE ticker=EDX{id}
├── llm/
│   ├── last_request.json     # последний prompt в Metric Extractor
│   ├── last_response.json    # последний ответ
│   └── cache_hits.txt        # grep "cache_hit_ratio" из лога
├── recent_commits.txt        # git log -20 (контекст изменений)
├── failure_taxonomy.json     # автоклассификация (см. ниже)
└── prompt.md                 # системный prompt для Claude Code
```

**Failure taxonomy** — пред-классификатор, который запускается до Claude
Code и подсказывает, на что смотреть. Считаем по pipeline.log + state:

| Код | Условие | Подсказка для Claude |
|---|---|---|
| `discoverer_403_servicepipe` | `discoverer_non_200` со status=403 | проверь cookies/Playwright |
| `discoverer_no_publications` | `discoverer_no_publications_for_type` для всех 4 типов | id может быть невалидным; проверь через find_e_disclosure_ids |
| `period_unparseable` | `period_parser_unmatched` в логе | расширь regex в `discoverer/period.py` |
| `classifier_other` | reporting_standard='OTHER' для всех документов | добавь маркеры в classifier/heuristics.py |
| `extract_text_too_short` | text-extract size < 1000 chars | проблема OCR — проверь Tesseract config или vision-fallback |
| `metric_coverage_zero` | metrics_rows=0 при machine_readable=1 | синонимы в metrics.yaml не покрывают терминологию этого эмитента |
| `metric_coverage_low` | 0 < coverage < 50% | расширь синонимы или добавь aggregation_hint |
| `unique_constraint` | `IntegrityError` UNIQUE constraint | dedup-баг в metric_extractor — Patch 26 регрессировал |
| `pipeline_crashed` | non-zero exit | exception в трейсе — фикси по traceback |
| `unknown` | ничего из вышеперечисленного | open-ended — пусть Claude разбирается |

Эта таксономия **не предписывает решение**, а сужает контекст для
агента: prompt template подставляет соответствующий совет.

**Что НЕ кладём в bundle (data hygiene):**

- секреты из `.env` (никогда)
- prompts/responses от Anthropic API в открытом виде, если содержат
  PDF-base64 (только метаданные usage)
- бинарные файлы PDF/ZIP — слишком тяжёлые

---

## 7.5 Долгосрочная память: `evolution/MEMORY.md`

Файл-журнал, который растёт по мере накопления опыта. Является
**обязательным input/output** для каждого Claude Code тика.

### 7.5.1 Структура файла

```markdown
# Self-Evolve Long-Term Memory

> Версионированный журнал решённых failure-классов и анти-паттернов.
> Читается агентом ДО любого изменения; обновляется ПОСЛЕ успешного патча.
> Сюда НЕ пишутся секреты, базы, бинарники.

## Index — solved failure classes

| failure_class | first_seen_tick | last_revisit_tick | applied_patches | solved? |
|---|---|---|---|---|
| period_unparseable_freeform | #5 | #12 | evolve(5), evolve(12) | partial |
| classifier_other_for_issuer  | #7 | — | evolve(7) | yes |
| metric_synonym_missing       | #3 | #18 | evolve(3,8,18) | partial |

## Patches log (reverse-chronological)

### evolve(N) — YYYY-MM-DD — failure_class
- **Tick:** #N — batch [EDX1210, EDX38588, EDX2541]
- **Failing companies:** EDX38588 (metric_coverage_zero)
- **Root cause:** ИЭК-Холдинг публикует МСФО под именем «иэк холдинг»
  в lowercase, а regex в `discoverer/period.py` ожидал uppercase.
- **Files touched:** `src/edx/stages/discoverer/period.py` (re.IGNORECASE)
- **Tests added:** `tests/stages/discoverer/test_period_iek_lowercase.py`
- **Anti-regression notes:**
  - DO NOT remove `re.IGNORECASE` from `_year_with_months` — повторно сломает
    «иэк холдинг» (id=38588).
  - DO NOT добавлять lowercase-ядро в search-mode БЕЗ префикса «за …» —
    в Patch 32 уже фиксировали false-positive по 4-значным числам в
    свободном тексте.
- **Coverage delta on batch:** EDX38588: 0% → 80%; EDX1210, EDX2541:
  no change (already OK).
- **Cost:** $0.83 (Claude Code session 0193abc-…).

### evolve(N-1) — YYYY-MM-DD — …
…

## Anti-patterns (extract)

- **NEVER** widen bank vocabulary in `metric_extractor/prompts.py` без
  re-валидации non_bank companies — false positives на nbsp-окончаниях
  (`«…банк-никойл»` → ложный `bank` profile в Patch evolve(11)).
- **NEVER** мокать сетевой Discoverer в e2e-тестах — раз в Patch evolve(15)
  это привело к тому, что коммит прошёл, а на проде VTBR провалил
  ServicePipe (мок не имитировал JS-challenge).

## Companies status (top 20)

| company_id | name | last_tick | verdict | metrics_count |
|---|---|---|---|---|
| 1210 | Банк ВТБ (ПАО) | #14 | ok | 5 |
| …    |   …            |  …  | … | … |
```

### 7.5.2 Жизненный цикл

```
┌─────────────────────────────────────────────────────────────────────┐
│ Тик N начинается:                                                   │
│   1. Bundle.assemble:                                               │
│      cp evolution/MEMORY.md → evolution/runs/{N}/memory_snapshot.md │
│   2. Claude Code запускается                                        │
│   3. Slash-command первой строкой говорит: «Read MEMORY.md»         │
│   4. Агент применяет патч                                           │
│   5. Slash-command финальной строкой: «Append to MEMORY.md»         │
│      - новая запись в Patches log                                   │
│      - обновлённый Index с новой failure_class                      │
│      - анти-регрессионные заметки                                   │
│ Verdict gate:                                                       │
│   проверяет diff(MEMORY.md, memory_snapshot.md) ≠ ∅                 │
│   иначе тик считается failed (claude забыл обновить)                │
└─────────────────────────────────────────────────────────────────────┘
```

### 7.5.3 Размер и ротация

- **Hard cap:** 200KB (≈ 2000 строк / 100 патчей).
- При достижении — отдельная команда `edx evolve memory compact` (ручная,
  не автоматическая) суммирует старшие 50% патчей в раздел «Archived
  lessons» и сохраняет full-history в `evolution/MEMORY.archive-{date}.md`.
- Compact НЕ запускается автоматически — оператор решает, когда (риск
  потери контекста, который ещё может понадобиться).

### 7.5.4 Что НЕ пишется в MEMORY.md

- ID конкретных публикаций (`SBER-3-1881246`) — это уровень bundle, не
  обобщения.
- Полный traceback — кладите номер commit'а, по нему всё восстановимо.
- API-ключи, паролия, пути к секретам — никогда.
- Оригинальные тексты документов (могут быть под NDA) — только сам
  паттерн.

---

## 8. Интеграция Claude Code

### 8.1 Установка на удалённом сервере

Часть `deploy/install_claude_code.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Node.js 20+ (требование Claude Code)
if ! command -v node >/dev/null || [[ "$(node -v | sed 's/v//' | cut -d. -f1)" -lt 20 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

# Claude Code CLI
sudo npm install -g @anthropic-ai/claude-code

# Проверка
claude --version
claude mcp list || true   # warm-up cache, не падает
```

### 8.2 Headless-режим

Claude Code запускается БЕЗ интерактивности:

```bash
claude -p "$(cat evolution/runs/{tick_id}/prompt.md)" \
    --output-format stream-json \
    --max-turns 25 \
    --permission-mode acceptEdits \
    --allowedTools "Read,Edit,Write,Bash(.venv/bin/python -m pytest *),Bash(.venv/bin/python -m ruff *),Bash(.venv/bin/python -m mypy src),Bash(.venv/bin/edx update --ticker EDX*),Bash(git diff *),Bash(git status *),Bash(git log *),Glob,Grep" \
    --add-dir /home/edx/VadimP/evolution/runs/{tick_id} \
    > evolution/runs/{tick_id}/claude.jsonl
```

**Ключевые ограничения** (через `.claude/settings.json` в проекте):

- `permissions.deny` хардкодит запреты:
  - `Bash(git push *)` — пуш делает Python-обёртка, не агент
  - `Bash(git reset --hard *)`
  - `Bash(rm -rf *)`
  - `Edit(.env*)`, `Write(.env*)`
  - `Edit(deploy/**)`, `Write(deploy/**)`
  - `Bash(curl *)`, `Bash(wget *)` — никаких экзотических зависимостей
  - `WebFetch`, `WebSearch` — без сетевых вызовов из агента
- `permissions.allow` явно разрешает `Read(./)`, `Edit(src/**)`,
  `Edit(config/**)`, `Edit(tests/**)`, `Edit(prompts/**)`,
  `Bash(.venv/bin/edx *)`, `Bash(.venv/bin/python *)`, `Bash(make *)`.

### 8.3 Custom slash command

Создаём `.claude/commands/edx-evolve-fix.md`:

```markdown
---
description: "Fix a batch of 3 e-disclosure tickers that the pipeline failed on"
argument-hint: "<tick_id>"
allowed-tools: ["Read", "Edit", "Write", "Bash(.venv/bin/python *)", "Bash(.venv/bin/edx update *)", "Bash(make *)", "Glob", "Grep"]
---

You are the self-evolve agent for the e-disclosure ETL pipeline.
The Diagnostic Bundle for this tick is at `evolution/runs/$1/`.

# STEP 0 — MANDATORY: Read the long-term memory FIRST

Before ANY analysis, read these files in order:
1. `evolution/MEMORY.md` — solved failure-classes, anti-patterns,
   recent patches log. **You MUST not introduce a fix that contradicts
   anti-patterns recorded here.**
2. `evolution/runs/$1/memory_snapshot.md` — frozen copy at tick start
   (used later to verify you actually updated MEMORY.md).
3. `evolution/runs/$1/batch.json` — the 3 companies in this tick
   and per-company verdicts.
4. `evolution/runs/$1/failure_taxonomy.json` — auto-classified hints
   (per company).
5. `evolution/runs/$1/pipeline.log.errors` — filtered errors.
6. `evolution/runs/$1/state-slice.sql` — state for these 3 tickers.
7. `PIPELINE_LOGIC.md` — pipeline architecture overview.

# STEP 1 — Diagnose

State concisely (in your scratchpad):
- Which of the 3 batch companies failed? Which succeeded?
- What is the most likely shared root cause? Or is each unique?
- Has this failure_class already appeared in MEMORY.md? If yes, what
  was tried before — and why didn't it solve THIS instance?

# STEP 2 — Fix (smallest possible change)

Make the smallest code change that:
- Fixes ≥1 failing company in the batch.
- Does NOT regress the other 2 companies in the batch.
- Does NOT regress canary tickers SBER, LKOH, IZNM.
- Does NOT introduce anti-patterns listed in MEMORY.md.

Constraints:
- DO NOT modify `.env`, `deploy/`, `evolution/runs/`, `.git/`, or any secret.
- DO NOT add new top-level Python dependencies unless absolutely necessary.
  If you do, justify it inline in the file you edit.
- DO NOT delete or restructure existing code beyond what the fix requires.
- DO NOT touch `tests/fixtures/` — committed fixtures are reality.
- DO NOT use `git push`, `git reset --hard`, or any branch operation —
  the wrapper handles git.

# STEP 3 — Validate

Run, in order, and STOP if any fails:
1. `make lint`
2. `make typecheck`
3. `make test`
4. `.venv/bin/edx update --config-dir config-evolve $(for c in $(cat evolution/runs/$1/batch.json | jq -r '.[].synthetic_ticker'); do echo "--ticker $c"; done)`

If a step fails, fix and re-run. If you can't fix in ≤3 turns, STOP.

# STEP 4 — MANDATORY: Update `evolution/MEMORY.md`

Append a new entry under `## Patches log (reverse-chronological)`:

    ### evolve($1) — {today's date} — {failure_class}
    - **Tick:** #$1 — batch [{ticker1}, {ticker2}, {ticker3}]
    - **Failing companies:** {list}
    - **Root cause:** {one paragraph}
    - **Files touched:** {paths}
    - **Tests added:** {paths or "none"}
    - **Anti-regression notes:**
      - DO NOT {specific don't-do-X items}
    - **Coverage delta on batch:** {per-company before→after}

If the failure_class is new, also add a row to `## Index` table.
If you discovered a new anti-pattern, add it to `## Anti-patterns`.

# STEP 5 — Final summary

Write to `evolution/runs/$1/SUMMARY.md`:

    # Tick #$1 summary
    - failure_class: ...
    - patch effect: ...
    - companies improved: [...]
    - companies neutral:  [...]
    - companies regressed: []   # MUST be empty
    - tests run: lint=ok typecheck=ok test=ok
    - memory updated: yes
    - cost USD (this turn): {if known}

DO NOT commit. DO NOT push. The wrapper does both after verifying the
gate.
```

### 8.4 Бюджеты

- **Per-tick LLM cost ceiling** для Claude Code: $2.00. Парсится из
  `claude.jsonl` (поле `total_cost_usd` в финальном `result`-сообщении).
  Превышение → `verdict=give_up`, skip-list.
- **Daily cap** для `edx evolve` (Anthropic API + Claude Code суммарно):
  $25/день. Хранится в `evolution_ticks` агрегатом за `started_at::date`;
  при превышении тики отдают `verdict=skipped_budget` без вызовов API.
- **Concurrency=1** строго: только один тик одновременно (flock).

---

## 9. Безопасность и инварианты

| Риск | Митигатор |
|---|---|
| Claude Code пушит сломанный код в master | Push в master идёт ТОЛЬКО Python-обёрткой после прохождения 4-уровневого gate: (1) make test зелёный; (2) make lint+typecheck зелёный; (3) re-run на батче улучшил метрики; (4) re-run на канарейках SBER/LKOH/IZNM не ухудшил. Любой провал → `git branch -D evolve/tick-N` без push. |
| Регрессия на ранее работавшем тикере | Канарейки + memory anti-patterns + batch-self-test (другие 2 тикера батча не должны деградировать). |
| Бесконечный цикл с одной компанией | `failure_count >= 3` → skiplist; ручной reset через CLI. |
| Утечка секретов в лог / коммит | Bundle никогда не включает `.env`. `.gitignore` дополняется `evolution/runs/`. Коммиты делает обёртка через `git add` со строгим whitelist (только src/, config/, tests/, prompts/, evolution/MEMORY.md). |
| Auto-evolve ломает state.sqlite | Перед каждым тиком: `cp state.sqlite state.sqlite.tick{N}.bak`. Откат — простое `mv`. Раз в неделю чистим бэкапы старше 14 дней. |
| Claude получает доступ к чужим файлам через Read | `--add-dir` указывает строго `evolution/runs/{tick_id}` + project root; никаких `~/`. |
| Пайплайн стучит на e-disclosure 100 раз/час | Per-tick rate limit ≤ 1 RPS уже в `app.yaml`. Daily — естественная гранулярность 12 тиков/час × 3 компании = 36 разных id × 24ч = 864 пика максимум, реально ≤ 300. |
| Конкурентный запуск с обычным `edx update` | flock на `/tmp/edx-state.lock`, общий между evolve-демоном и cron-таймером. |
| Claude Code случайно подключился не к тому Anthropic-аккаунту | Отдельная переменная `CLAUDE_CODE_OAUTH_TOKEN` (не путать с `ANTHROPIC_API_KEY` пайплайна), отдельный billing-счётчик. |
| Память (`MEMORY.md`) расходится с реальностью кода (anti-pattern удалили физически, но запись осталась) | `edx evolve memory verify` (manual CLI) — сверяет упомянутые файлы/функции с текущим деревом, помечает stale-записи. |
| Агент пишет ложный summary в MEMORY.md, чтобы пройти gate | Gate проверяет: (a) diff не пустой; (b) добавилась запись с шаблоном `### evolve(N) — `; (c) re-run действительно улучшил батч (двойной контроль). |
| Auto-merge ломает master в выходные | Не блокируем — план явно утверждает автономность. Митигатор: канарейки + tests gate. Если оператор обнаружит регрессию — `git revert {sha}` руками; следующий тик увидит изменение в MEMORY.md (обёртка пишет revert-комментарий). |

---

## 10. CLI: новые команды

```
edx evolve tick                      # одна итерация (вызывается systemd)
edx evolve status [--limit N]        # последние тики из evolution_ticks
edx evolve reset --company-id ID     # снять с skiplist
edx evolve replay --tick-id N        # повторить тик из bundle (без LLM, для отладки)
edx evolve report                    # сводка: % успехов, $/tick, кол-во патчей в master
edx evolve memory show               # печатает evolution/MEMORY.md
edx evolve memory verify             # сверяет MEMORY.md с деревом — помечает stale-записи
edx evolve memory compact            # ручная компакция (см. §7.5.3)
```

**Важно:** `edx evolve tick` НЕ вызывается оператором руками в норме —
это работа systemd-таймера. Но команда доступна для отладки.

---

## 11. Расписание

`deploy/systemd/edx-evolve.service`:

```ini
[Unit]
Description=edx self-evolve tick
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/edx
ExecStartPre=/usr/bin/flock -n /tmp/edx-evolve.lock /bin/true
ExecStart=/usr/bin/flock /tmp/edx-evolve.lock /opt/edx/.venv/bin/edx evolve tick
EnvironmentFile=-/opt/edx/.env.evolve
User=edx
Group=edx
TimeoutStartSec=45min
```

`deploy/systemd/edx-evolve.timer`:

```ini
[Unit]
Description=Trigger edx evolve every 5 minutes
Requires=edx-evolve.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
RandomizedDelaySec=15s
AccuracySec=15s
Unit=edx-evolve.service

[Install]
WantedBy=timers.target
```

`/opt/edx/.env.evolve` хранит **отдельный** ключ для Claude Code, чтобы
лимиты не пересекались с боевым `ANTHROPIC_API_KEY`:

```
ANTHROPIC_API_KEY=sk-ant-api03-…    # для пайплайна
CLAUDE_CODE_OAUTH_TOKEN=…           # для headless Claude Code
EDX_EVOLVE_DAILY_BUDGET_USD=25
EDX_EVOLVE_TICK_BUDGET_USD=2
EDX_EVOLVE_BRANCH=auto-evolve
```

---

## 12. Развёртывание на удалённом сервере

Существующая VPS-инсталляция (см. `README.md §6`) уже даёт каркас.
Дополнительно нужно:

```bash
# 1. Установить Claude Code (см. §8.1)
sudo bash deploy/install_claude_code.sh

# 2. Залогиниться от имени edx-юзера
sudo -iu edx
claude /login                 # interactive 1 раз; токен ляжет в ~/.claude
exit

# 3. Прокинуть ENV
sudo cp deploy/env.evolve.example /opt/edx/.env.evolve
sudo $EDITOR /opt/edx/.env.evolve   # вписать токены и budgets

# 4. Установить таймер
sudo cp deploy/systemd/edx-evolve.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now edx-evolve.timer

# 5. Создать ветку для авто-патчей
sudo -iu edx
cd /opt/edx
git checkout -b auto-evolve
git push -u origin auto-evolve

# 6. Branch protection на master (через GitHub UI или gh):
# Запретить direct push, требовать PR review.
```

---

## 13. План внедрения по фазам

| Patch | Что делает | Готовность к merge |
|---|---|---|
| **38** | миграция `0010_evolution.sql`; модели `EvolutionTick`, `EvolutionSkiplist`; репозиторий `evolution_repo.py`; smoke-тест на schema. Создание пустого `evolution/MEMORY.md` с шаблоном. | независимо |
| **39** | модуль `src/edx/evolve/csv_loader.py` (загрузка CSV с колонкой type), `src/edx/evolve/picker.py` (батч=3, MOEX-overlap skip, cooldown), `src/edx/evolve/synth.py` (запись `config-evolve/tickers.yaml`). Юнит-тесты. | независимо |
| **40** | `evolve/snapshot.py`, `evolve/runner.py` (subprocess `edx update --ticker A --ticker B --ticker C`), `evolve/verdict.py` (per-company verdicts). CLI: `edx evolve tick` (без Claude Code — на провале просто складывает bundle). | можно тестить без LLM |
| **41** | `evolve/bundle.py` (Diagnostic Bundle для батча), `evolve/taxonomy.py` (per-company классификация), `evolve/canaries.py` (snapshot SBER/LKOH/IZNM). Прогон вручную по 3 батчам из CSV, инспекция bundle'ов. | блокирует 42 |
| **42** | `evolve/memory.py` (read/append `evolution/MEMORY.md`, проверка изменений после тика). `evolve/claude_runner.py` (subprocess.run для `claude -p`, парсинг stream-json, бюджеты, таймауты). `.claude/settings.evolve.json`. `.claude/commands/edx-evolve-fix.md`. | блокирует 43 |
| **43** | `evolve/git_ops.py` — fast-forward merge в master, push, `git branch -D` для отказа. Полный verdict gate (tests + canaries + batch + memory_updated). CLI `edx evolve status/replay/report/reset` + `edx evolve memory show/verify/compact`. | блокирует 44 |
| **44** | Deploy: `deploy/install_claude_code.sh` (Node 20 + npm install -g @anthropic-ai/claude-code), `deploy/systemd/edx-evolve.{service,timer}`, `deploy/env.evolve.example`. Раздел «Self-Evolve» в README. | блокирует 45 |
| **45** | Pilot на тест-VPS: оператор вручную запускает 5 тиков, инспектирует MEMORY.md и коммиты. Тюнит budget cap, max_turns, размер батча если нужно. | требует ручного пилота |
| **46** | Включение `edx-evolve.timer` в проде, branch protection master ОТКЛЮЧЕН (auto-merge), monitoring через `edx evolve report`. «Operator runbook» — как откатить плохой коммит, как сбросить skiplist, как почистить MEMORY.md. | финиш |

Каждый patch == один обычный коммит/PR оператора в master. Серия 38–43
сама по себе не запускает auto-push (демон выключен по умолчанию), так
что весь код можно безопасно влить до того, как timer включится в Patch 46.

---

## 14. Метрики успеха

После 2 недель работы (≈ 4000 тиков):

| Метрика | Target |
|---|---|
| Coverage of `e-disclosure-companies.csv` (хотя бы один OK-тик) | ≥ 80% |
| Companies in skiplist | ≤ 15% |
| Average claude_cost_usd per evolved-fix tick | ≤ $1.20 |
| Daily evolve budget overruns | 0 |
| Patches landed in master after PR review | ≥ 5 |
| Patches reverted (regression caught after merge) | 0 |
| `make test` red rate after auto-commit | < 2% |
| Time-to-evolve median (failed → first ok) | ≤ 4 тика на компанию |

---

## 15. Согласованные решения (закрыто оператором 2026-05-03)

| # | Вопрос | Решение |
|---|---|---|
| 1 | Ветка для авто-коммитов | **Auto-merge в `master`** — эволюция автономна. Временная ветка `evolve/tick-N` существует только на время тика и удаляется (push'ом или branch -D). |
| 2 | Дневной бюджет | **$25/день** жёсткий cap. Tick cap — $2. |
| 3 | Профиль bank/non_bank | **Из колонки `type` в CSV** — никакой эвристики. CSV имеет 3 колонки: `id, name, type`. |
| 4 | MOEX-overlap | **Пропускаем** в evolve-демоне id, замапленные в основном `config/tickers.yaml` (SBER, LKOH, …) — они идут через обычный `edx update`. |
| 5 | Размер батча | **3 компании на тик** — Claude Code видит 3 разных провала и предлагает обобщённый фикс. Также — 3-way anti-regression проверка внутри батча. |
| 6 | Долгосрочная память | **Файл `evolution/MEMORY.md`**. Обязателен к чтению Claude Code в STEP 0 и обязателен к обновлению в STEP 4. Без обновления тик считается failed. |

---

## Приложение A: пример `prompt.md` для Claude Code

```markdown
# Diagnostic Bundle: tick #{tick_id}

Company: {name} (id={company_id}), ticker={ticker}, profile={profile}
Started: {started_at}
Failure class (auto-detected): {taxonomy_code}

## Hypothesis (from taxonomy)
{taxonomy_hint}

## What the pipeline did
- Discoverer: {discoverer_summary}
- Downloader: {downloader_summary}
- Classifier: {classifier_summary}
- Text Extractor: {text_extractor_summary}
- Metric Extractor: {metric_extractor_summary}

## Snapshot diff
- publications: before={p_before}, after={p_after}
- documents: before={d_before}, after={d_after}
- metrics rows: before={m_before}, after={m_after}
- qa_issues: before={q_before}, after={q_after}

## Files in this bundle
- pipeline.log.errors  (filtered log, level=error/warning)
- state-slice.sql      (only this ticker's rows)
- llm/last_request.json, llm/last_response.json
- failure_taxonomy.json

## Your task
{slash_command_body}
```

---

## Приложение B: матрица ответственности (RACI)

| Действие | Оператор | Pipeline | Evolve runner | Claude Code |
|---|---|---|---|---|
| Выбор следующей компании | I | — | R, A | — |
| Запуск ETL на одной компании | I | R | A | — |
| Сборка Diagnostic Bundle | — | — | R, A | I |
| Поиск root cause + патч | C | — | I | R, A |
| Запуск тестов и валидация | — | — | R | C |
| Коммит в `auto-evolve` | — | — | R, A | I |
| Push в `auto-evolve` | — | — | R, A | — |
| Merge в master | R, A | — | C | — |
| Reset skiplist | R, A | — | I | — |

R=Responsible, A=Accountable, C=Consulted, I=Informed.
