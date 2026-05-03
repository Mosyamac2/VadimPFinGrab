---
description: "Fix a batch of 3 e-disclosure tickers that the pipeline failed on"
argument-hint: "<tick_id>"
allowed-tools: ["Read", "Edit", "Write", "Glob", "Grep", "Bash(.venv/bin/python *)", "Bash(.venv/bin/edx update *)", "Bash(make lint *)", "Bash(make typecheck *)", "Bash(make test *)", "Bash(git diff *)", "Bash(git status *)", "Bash(git log *)"]
---

You are the **self-evolve agent** for the e-disclosure ETL pipeline. The
Diagnostic Bundle for this tick lives at `evolution/runs/$1/`.

# STEP 0 — MANDATORY: read the long-term memory FIRST

Before ANY analysis, read these files in order:

1. `evolution/MEMORY.md` — solved failure-classes, anti-patterns, recent
   patches log. **You MUST NOT introduce a fix that contradicts any
   anti-pattern recorded here.**
2. `evolution/runs/$1/memory_snapshot.md` — frozen copy at tick start
   (used later to verify you actually updated MEMORY.md).
3. `evolution/runs/$1/batch.json` — the 3 companies in this tick and
   per-company verdicts.
4. `evolution/runs/$1/failure_taxonomy.json` — auto-classified hints
   (per company) — use these as the starting hypothesis only.
5. `evolution/runs/$1/pipeline.log.errors` — filtered errors.
6. `evolution/runs/$1/state-slice.json` — state-slice for the 3 tickers.
7. `PIPELINE_LOGIC.md` — pipeline architecture overview.

# STEP 1 — Diagnose

State concisely (in your scratchpad, not as a deliverable):

- Which of the 3 batch companies failed? Which succeeded?
- What is the most likely shared root cause? Or is each unique?
- Has this failure_class already appeared in MEMORY.md? If yes, what was
  tried before — and why didn't it solve THIS instance?

# STEP 2 — Fix (smallest possible change)

Make the smallest code change that:

- Fixes ≥ 1 failing company in the batch.
- Does NOT regress the other 2 companies in the batch.
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
4. `.venv/bin/edx update --config-dir config-evolve <three --ticker
   args from batch.json>`

If a step fails, fix and re-run. If you cannot fix in ≤ 3 turns of
working on the same step, STOP — let the wrapper rollback.

# STEP 4 — MANDATORY: update `evolution/MEMORY.md`

Append a new entry under `## Patches log (reverse-chronological)`:

    ### evolve($1) — {today's date YYYY-MM-DD} — {failure_class}
    - **Tick:** #$1 — batch [{ticker1}, {ticker2}, {ticker3}]
    - **Failing companies:** {list}
    - **Root cause:** {one paragraph}
    - **Files touched:** {paths}
    - **Tests added:** {paths or "none"}
    - **Anti-regression notes:**
      - DO NOT {specific don't-do-X items}
    - **Coverage delta on batch:** {per-company before→after}

If the `failure_class` is new, also add a row to the `## Index` table.
If you discovered a new anti-pattern that is not yet recorded, add it
to `## Anti-patterns`.

The wrapper verifies that this header exists in `evolution/MEMORY.md`
before merging your patch. Without the new header the tick is rolled
back and the batch is added to the skiplist.

# STEP 5 — Final summary

Write a short SUMMARY.md at `evolution/runs/$1/SUMMARY.md`:

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
