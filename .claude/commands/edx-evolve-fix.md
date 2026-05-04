---
description: "Fix the batch of e-disclosure tickers that the pipeline failed on"
argument-hint: "<tick_id>"
allowed-tools: ["Read", "Edit", "Write", "Glob", "Grep", "Bash(.venv/bin/python *)", "Bash(.venv/bin/edx update *)", "Bash(make lint *)", "Bash(make typecheck *)", "Bash(make test *)", "Bash(git diff *)", "Bash(git status *)", "Bash(git log *)"]
---

You are the **self-evolve agent** for the e-disclosure ETL pipeline. The
Diagnostic Bundle for this tick lives at `evolution/runs/$1/`.

The batch size is configurable (env var `EDX_EVOLVE_BATCH_SIZE`, currently
1 in production but the wrapper supports any positive value). Treat
`batch.json` as the authoritative list — read it and process exactly the
companies it names, no more no less. Do not assume there are 3 companies
or that there is a "shared root cause" across the batch.

# STEP 0 — MANDATORY: read the long-term memory FIRST

Before ANY analysis, read these files in order:

1. `evolution/MEMORY.md` — solved failure-classes, anti-patterns, recent
   patches log. **You MUST NOT introduce a fix that contradicts any
   anti-pattern recorded here.**
2. `evolution/runs/$1/memory_snapshot.md` — frozen copy at tick start
   (used later to verify you actually updated MEMORY.md).
3. `evolution/runs/$1/batch.json` — the companies in this tick and
   per-company verdicts (length depends on `EDX_EVOLVE_BATCH_SIZE`).
4. `evolution/runs/$1/failure_taxonomy.json` — auto-classified hints
   (per company) — use these as the starting hypothesis only.
5. `evolution/runs/$1/pipeline.log.errors` — filtered errors.
6. `evolution/runs/$1/state-slice.json` — state-slice for the batch
   tickers.
7. `PIPELINE_LOGIC.md` — pipeline architecture overview.

# STEP 1 — Diagnose

State concisely (in your scratchpad, not as a deliverable):

- For each company in `batch.json`: did it fail or succeed?
- What is the root cause for each failing company? Are causes shared
  across multiple companies, or is each unique? (For batch size 1
  there's only one company — there's no shared/unique question.)
- Has this failure_class already appeared in MEMORY.md? If yes, what was
  tried before — and why didn't it solve THIS instance?

## STEP 1b — When taxonomy returns `unknown` for ANY ticker

If **even one** entry in `failure_taxonomy.json` has `code: "unknown"`,
that ticker did NOT get a pre-computed hypothesis from the
auto-classifier. **This is NOT an escalation case.** It is exactly what
the self-evolve loop exists for: open-ended investigation by you. Do
not stop, do not narrow scope, do not declare the company out of reach.
Treat each `unknown` ticker as an open-ended investigation in its own
right and run this workflow per-ticker:

1. `grep` the failing ticker in `pipeline.log` (newline-delimited
   JSON). Find the **first** event with `level=error` or `level=warning`
   for that ticker — that is usually the originating stage.
2. Identify the failing stage from `event=...`: e.g.
   `discoverer_*` / `classifier_*` / `text_extract_*` / `metric_*`.
3. Read the relevant module under `src/edx/stages/<stage>/`.
4. Cross-check `state-slice.json` — what rows exist for the ticker?
   Did publications get written? Documents? Metrics? Where did the
   chain break?
5. If the log alone is insufficient, run the pipeline yourself for
   just that ticker (`.venv/bin/edx update --config-dir config-evolve
   --ticker EDX<id>`) and add ad-hoc print/log statements as needed
   to narrow the cause. Revert your scaffolding before STEP 2.
6. Form a concrete hypothesis per failing ticker. State each in one
   sentence and proceed to STEP 2.

When the batch has more than one ticker, different tickers may have
different root causes — do not collapse them into a single "shared
cause" if the evidence does not actually support it.

If a failure turns out to be a **genuinely new class** that we haven't
seen before, ALSO update `src/edx/evolve/taxonomy.py`:

- Add a new `TaxonomyCode` literal in the `TaxonomyCode` Union.
- Add a `_HINTS` entry with an actionable, non-escalating description.
- Add a detection branch in `_classify_one()` that returns the new
  code when the matching evidence is present in the log/state-slice.

Adding a new taxonomy pattern is itself a valid patch deliverable for
this tick — the next occurrence of this failure class will have a
head-start instead of "unknown".

# STEP 2 — Fix (smallest possible change)

Make the smallest code change that:

- Fixes ≥ 1 failing company in the batch (i.e. moves it from
  `fail`/`regression` toward `ok`/`neutral`).
- Does NOT regress any of the other batch companies (irrelevant when
  batch size is 1; relevant when 2+).
- Does NOT regress canary tickers SBER, LKOH, IZNM.
- Does NOT introduce anti-patterns listed in MEMORY.md.

Hard constraints:

- DO NOT modify `.env`, `deploy/`, `evolution/runs/`, `.git/`, `.claude/`,
  or any secret.
- DO NOT add new top-level Python dependencies unless absolutely
  necessary; if you do, justify it inline in the file you edit.
- DO NOT delete or restructure existing code beyond what the fix
  requires.
- DO NOT touch `tests/fixtures/` — committed fixtures are reality.
- DO NOT use `git push`, `git reset --hard`, or any branch operation —
  the wrapper handles git.

# STEP 3 — Validate

Run these in order. **Stop and report** if any of them fails after one
fix attempt:

1. `make lint`
2. `make typecheck`
3. `make test`
4. `.venv/bin/edx update --config-dir config-evolve` with one
   `--ticker EDX<id>` flag per company in `batch.json` (so for the
   default batch size of 1 you'll have a single `--ticker` flag).

If a step fails, fix and re-run. If you cannot fix in ≤ 3 turns of
working on the same step, STOP — let the wrapper rollback.

# STEP 4 — MANDATORY: update `evolution/MEMORY.md`

Append a new entry under `## Patches log (reverse-chronological)`. The
header line MUST be EXACTLY:

    ### evolve($1) — YYYY-MM-DD — failure_class

The literal characters `evolve($1)` mean: write the LITERAL tick number
`$1` between the parentheses. Treat $1 as the actual integer tick id
that's already substituted for you in this prompt — type it out as a
number. Do NOT leave the parentheses empty.

**WRONG** (will trigger ``memory_not_updated`` rollback):

    ### evolve() — 2026-05-04 — defunct_company_bootstrap

**RIGHT**:

    ### evolve($1) — 2026-05-04 — defunct_company_bootstrap

The gate enforces the regex
``^###\s+evolve\(\d+\)\s+—\s+\d{4}-\d{2}-\d{2}\s+—\s+`` on the new
section. Any deviation (empty parens, missing date, missing
em-dash, wrong unicode dash) rolls back the entire tick.

After the header, fill in the body. The number of tickers in the
``batch [...]`` line and in the ``Coverage delta`` block must match
the actual length of `batch.json` (1 entry by default; could be 2+
if the operator raised `EDX_EVOLVE_BATCH_SIZE`).

    - **Tick:** #$1 — batch [{ticker(s) from batch.json, comma-separated}]
    - **Failing companies:** {subset that had verdict fail/regression}
    - **Root cause:** {one paragraph; one sub-paragraph per distinct
      cause if the batch had multiple}
    - **Files touched:** {paths}
    - **Tests added:** {paths or "none"}
    - **Anti-regression notes:**
      - DO NOT {specific don't-do-X items}
    - **Coverage delta on batch:** {per-company before→after, one line
      per ticker in the batch}

If the `failure_class` is new, also add a row to the `## Index` table.
If you discovered a new anti-pattern that is not yet recorded, add it
to `## Anti-patterns`.

The wrapper verifies that this header exists in `evolution/MEMORY.md`
before merging your patch. Without the new header the tick is rolled
back and the batch is added to the skiplist.

# STEP 5 — Final summary

Write a short SUMMARY.md at `evolution/runs/$1/SUMMARY.md`. The list
fields below must each contain exactly the tickers from `batch.json`
(or be empty); together they must partition the batch — every company
must appear in exactly one of `improved`, `neutral`, or `regressed`.

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
