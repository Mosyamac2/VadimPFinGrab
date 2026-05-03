# Self-Evolve Pilot Report — template (Patch 45)

> Заполняется оператором по итогам пилотного прогона на тест-VPS.
> Цель: убедиться, что demon работает в режиме dry-run и в режиме
> agent-on без неожиданностей, ДО включения на проде (Patch 46).
>
> Файл — однократный, после анализа можно удалить.

## Phase 1 — dry-run (24 часа, EDX_EVOLVE_AGENT_ENABLED=0)

| Метрика | Ожидание | Факт |
|---|---|---|
| Тиков за 24 ч | 50–60 | _____ |
| Среднее время одного тика (мин) | < 15 | _____ |
| Размер каждого `evolution/runs/<N>/` | 2–20 MB | _____ |
| Аномалии (>100 MB) | 0 | _____ |
| Тиков с verdict=ok | ≥ 40% | _____ |
| Тиков с verdict=fail | ≥ 30% | _____ |
| Бюджет за 24 ч | $0 | _____ |

Spot-checked bundles (5 штук — 1×ok, 1×neutral, 2×fail, 1×regression):

| tick_id | verdict | adequate taxonomy? | adequate state-slice? | notes |
|---|---|---|---|---|
| #__ | __ | __ | __ | |
| #__ | __ | __ | __ | |
| #__ | __ | __ | __ | |
| #__ | __ | __ | __ | |
| #__ | __ | __ | __ | |

## Phase 2 — agent-on (5 ticks under supervision)

| tick_id | failing → improved | tests gate | canary gate | gate verdict | cost USD | turns |
|---|---|---|---|---|---|---|
| #__ | __ → __ | __ | __ | __ | $___ | __ |
| #__ | __ → __ | __ | __ | __ | $___ | __ |
| #__ | __ → __ | __ | __ | __ | $___ | __ |
| #__ | __ → __ | __ | __ | __ | $___ | __ |
| #__ | __ → __ | __ | __ | __ | $___ | __ |

Whitelist violations observed (must be 0):
- _____

Master commits with prefix `evolve(N):` after pilot:
- _____ (sha) — _____  (per-ticker improvement)
- _____ (sha) — _____
- _____ (sha) — _____

`evolution/MEMORY.md` entries added during pilot:
- _____

## Phase 3 — calibration

Snapshot the medians for the 5 agent-on ticks above.

| Metric | Median | Max | Recommended cap |
|---|---|---|---|
| cost USD | _____ | _____ | _____ |
| turns | _____ | _____ | _____ |
| wall-time (min) | _____ | _____ | _____ |
| bundle size (MB) | _____ | _____ | _____ |

Decisions:
- [ ] Daily cap stays at $25 / lower to $___ / raise to $___ (justify).
- [ ] Tick cap stays at $2 / change to $___.
- [ ] Cooldown stays at 7 days / change to ___ days.
- [ ] Batch size stays at 3 / change to ___.

## Phase 4 — Code tweaks (small Patch 45 mini-PRs)

If new failure-classes were observed in real logs that fall into
`unknown`, extend `src/edx/evolve/taxonomy.py` here. Each item below
is a mini-PR within Patch 45.

- [ ] _____  (file: __, ticket: __)
- [ ] _____
- [ ] _____

## Phase 5 — Sign-off

- [ ] Phase 1 metrics within tolerance.
- [ ] Phase 2: 0 master regressions caught manually.
- [ ] Phase 3: caps confirmed.
- [ ] Phase 4: tweaks merged.
- [ ] `edx evolve memory verify` — stale = 0 OR known and acceptable.
- [ ] Operator signature: ____  Date: ____

After sign-off → Patch 46 enables the timer in production.
