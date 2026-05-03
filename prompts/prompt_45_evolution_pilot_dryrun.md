# Промпт 45. Самоэволюция: пилот в dry-run режиме на тест-VPS

> Зависимости: Patch 38–44 в master, demon установлен и крутится в
> dry-run на тест-VPS (`EDX_EVOLVE_AGENT_ENABLED=0`).
> Этот патч — **процедура валидации**, а не код. Реальные изменения
> кода допустимы только как «point-fixes» по результатам наблюдения.

## Цель

1. Прогнать ≥ 30 dry-run тиков на тест-VPS, инспектировать каждый
   bundle вручную.
2. Выявить и устранить:
   - false-positive failure-classes (Patch 41 taxonomy);
   - bundle-файлы > expected size;
   - тики, в которых Picker зацикливается на одной компании;
   - случаи когда `compute_verdict` даёт неинтуитивный результат.
3. Прогнать ≥ 5 «контрольных» тиков с **EDX_EVOLVE_AGENT_ENABLED=1**
   и реальным `CLAUDE_CODE_OAUTH_TOKEN` под присмотром оператора —
   ассистент создаёт коммит, gate запускается, master либо получает
   правку либо тик откатывается. Каждый из этих 5 тиков — записан в
   MEMORY.md.
4. Откалибровать бюджеты:
   - per-tick cap (стартовый $2 — может быть низок для классов с
     большой нагрузкой на Read);
   - daily cap (стартовый $25 — посмотреть, во сколько обходится
     реальный день).
5. Подготовить «runbook tweak»-PR (этот патч) с любыми мелкими
   фиксами (config, docstrings, дополнительные taxonomy-коды).

## Процедура

### Phase 1 — dry-run (24 часа)

```bash
# на VPS, под edx
sudo systemctl status edx-evolve.timer       # должен быть active (waiting)
journalctl -u edx-evolve.service --since "24 hours ago"
.venv/bin/edx evolve status --limit 50
```

Что проверять каждые 4–6 часов:

1. `evolution/runs/` — должно быть ~50–60 каталогов за 24ч (12 тиков/час
   × 4-6 часов реального availability с учётом cooldowns/skiplist).
2. `du -sh evolution/runs/*` — каждый каталог 2–20 MB. Если есть
   аномалии >100 MB → debug, какой-то лог распух.
3. Бюджет дневной — должен быть $0 (агент выключен).
4. `verdict` распределение: `ok` / `neutral` / `fail` / `regression`.
   Ожидаем разумно: ≥ 40% ok, ≥ 30% fail (новые компании, на которых
   пайплайн ещё не справляется).

### Phase 2 — спот-инспекция bundle'ов

Случайно выбрать 5 тиков (1× ok, 1× neutral, 2× fail, 1×
regression-tests / regression). Для каждого:

```bash
cd evolution/runs/<N>/
ls -la
cat batch.json | jq '.'
cat failure_taxonomy.json | jq '.'
head -200 pipeline.log.errors
```

Контроль:
- `pipeline.log.errors` ~ 2-50 KB (фильтрация работает);
- `state-slice.txt` читается человеком;
- `failure_taxonomy.json` адекватен (не «unknown» там, где явно
  виден period_unparseable);
- `memory_snapshot.md` совпадает с `evolution/MEMORY.md` на момент
  тика (в Phase 1 MEMORY.md не должен меняться).

Любая аномалия → создать issue / fix в этом же патче (MR в
`src/edx/evolve/{taxonomy,bundle}.py`, etc.).

### Phase 3 — agent-on, под надзором (5 тиков)

```bash
sudo $EDITOR /opt/edx/.env.evolve
# EDX_EVOLVE_AGENT_ENABLED=1
sudo systemctl restart edx-evolve.timer
```

Внимательно смотреть journalctl + `evolution/runs/<N>/` ПОСЛЕ каждого
тика:

| Что проверить | Где |
|---|---|
| Агент стартанул и записал claude.jsonl | `evolution/runs/<N>/claude.jsonl` |
| MEMORY.md обновился (если коммит был) | `git log -1 -- evolution/MEMORY.md` |
| Whitelist не нарушен | проверить `git show <sha> --stat` — пути ∈ allowed-globs |
| Канарейки не сломались | `edx evolve status` → нет verdict='regression_canary' |
| Откат веток отработал на FAIL | `git branch -a` — нет `evolve/tick-*` остатков |

Если за 5 тиков ВСЁ корректно — отключить ручной надзор.

Если на любом провал — вернуть `EDX_EVOLVE_AGENT_ENABLED=0`,
закоммитить fix отдельным малым PR, цикл повторить.

### Phase 4 — calibration

Из 5 тиков снять реальные значения:

| Метрика | Median | Max |
|---|---|---|
| Cost per agent tick (USD) | … | … |
| Turns | … | … |
| Wall-time (мин) | … | … |
| Bundle size (MB) | … | … |

Если median cost > $1.50 → понизить `EDX_EVOLVE_TICK_BUDGET_USD` до 3$
(временно), чтобы покрыть хвост.
Если daily projection > $25 → ужесточить cooldown / batch size.
Если turns > 20 систематически → сократить slash-команду.

### Phase 5 — code tweaks (если нужны)

Любые коррекции из Phases 1-4:

- Расширение `taxonomy.py` новыми кодами, увиденными в реальных логах.
- Корректировка `compute_verdict` (новые edge cases).
- Жёсткие caps в `bundle.py` (max log size, max evidence count).
- Уточнение `_compose_commit_message` если оператор хочет другие поля.

Каждый tweak — отдельный мини-PR (внутри Patch 45). Все изменения
**только** в файлах `src/edx/evolve/*` и `tests/evolve/*`. Не трогаем
deploy/, README, MEMORY.md.

## Acceptance criteria

- ≥ 30 dry-run тиков прогнаны без crash'а edx-evolve.service.
- ≥ 5 agent-on тиков прошли с прозрачным auditom (либо ok с
  master-коммитом, либо fail с откатом ветки).
- `edx evolve report` за период пилота сошёлся: cost ≤ daily cap,
  Distribution verdict-ов осмыслен, skiplist не разросся > 10% компаний.
- В `evolution/MEMORY.md` ≥ 1 запись `### evolve(N) — …`,
  написанная агентом.
- 0 ручных вмешательств в master, кроме PR этого патча.

## Что НЕ входит в Phase 5

- Не расширяем `permissions.allow` в `.claude/settings.evolve.json`.
- Не отключаем canary check.
- Не повышаем daily budget без отдельного согласования с оператором.

## Артефакты на выход

- `pilot_report.md` (одноразовый файл в корне репо, потом удалить)
  с таблицами Calibration + список всех увиденных failure-classes.
- Минимум 1 новый параграф в `evolution/MEMORY.md` (anti-pattern,
  выявленный в пилоте).
- Минимум 1 новый тест в `tests/evolve/` (на регрессию любого fix'а
  Phase 5).

## Что класть в MEMORY.md

(Появляется только во время пилота — реальные кейсы.)

Возможные сценарии-«грабли», на которые стоит заранее подготовиться:

- «Picker берёт батч из 3 компаний, у двух из которых одинаковый
  failure-class → Claude применяет точечный фикс под одну, ломает
  третью» — митигатор: gate test + canary; в MEMORY.md фиксируем
  «не делать локальный bypass без unit-test'а на 3-ю компанию».
- «Агент пишет MEMORY.md, но запись с тем же `### evolve(N)` уже
  существует (replay)» — has_new_entry_since должен это ловить;
  если не ловит, поправить в Patch 45.
