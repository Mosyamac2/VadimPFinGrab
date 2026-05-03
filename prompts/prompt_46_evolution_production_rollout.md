# Промпт 46. Самоэволюция: production-rollout, monitoring, Operator runbook

> Зависимости: Patch 38–45. Pilot закрыт. Все calibrate-фиксы
> в master.
> Этот патч — финальный: переключает evolve из «оператор-надзор» в
> «полностью автономный», добавляет SLO-мониторинг и финальный
> operator runbook.

## Цель

1. Включить `EDX_EVOLVE_AGENT_ENABLED=1` на проде.
2. Завести лёгкий мониторинг: ежедневный сводный отчёт `edx evolve
   report` через cron, log-rotate `evolution/runs/`.
3. Финализировать `USER_GUIDE.md` секцию «Self-Evolve» с runbook'ом
   реакции на инциденты.
4. Финальный диагностический тест suite — smoke на live state.sqlite,
   чтобы при апгрейде Python/Anthropic SDK сразу падал.

## Контекст

- Pilot прошёл: median cost agent tick ≤ $1.50, daily projection ≤ $20,
  ≥ 5 agent коммитов в master, 0 канарейных регрессий.
- Оператор контролирует только через `edx evolve status` / `report`
  и git log.
- Никаких новых фич — только enable + housekeeping.

## Задачи

### 1. Production env

`/opt/edx/.env.evolve` обновляется (operator-action):

```dotenv
EDX_EVOLVE_AGENT_ENABLED=1
# (всё остальное как было)
```

```bash
sudo systemctl restart edx-evolve.timer
```

В коде ничего не меняется — этот шаг исполняется руками оператора.
Однако фиксируем в репо `deploy/PRODUCTION_ENABLE.md` checklist:

```markdown
# Production rollout — Self-Evolve

Дата: ___
Оператор: ___

- [ ] `edx evolve report` за последнюю неделю prelim показал ≥ 5 ok тиков.
- [ ] Median cost ≤ EDX_EVOLVE_TICK_BUDGET_USD * 0.75.
- [ ] Daily peak ≤ EDX_EVOLVE_DAILY_BUDGET_USD * 0.6.
- [ ] `evolution/MEMORY.md` ≥ 5 записей.
- [ ] Skiplist (`edx evolve status`) ≤ 10% компаний.
- [ ] `edx evolve canary capture` повторён в течение 24 часов перед
       enable'ом (актуальный baseline).
- [ ] `git log --grep='evolve(' master | wc -l` ≥ 5.
- [ ] Бэкап `data/state.sqlite` сохранён.
- [ ] Бэкап `evolution/MEMORY.md` сохранён.
- [ ] PRODUCTION_ENABLE.md закоммичен с подписью оператора.
- [ ] Изменена переменная `EDX_EVOLVE_AGENT_ENABLED=1`.
- [ ] `systemctl restart edx-evolve.timer` выполнен.
- [ ] Спустя 24 часа: `edx evolve report` подтверждает auto-merge'и.
```

### 2. Daily summary cron

`deploy/cron/edx-evolve-summary.crontab`:

```
# Patch 46: morning summary of overnight evolve activity.
# Sends `edx evolve report` to a local log; operator can pipe it to email
# / Telegram / Slack via their own automation.

0 8 * * * /usr/bin/env -i HOME=$HOME PATH=/usr/local/bin:/usr/bin:/bin /opt/edx/.venv/bin/edx evolve report > /opt/edx/logs/evolve-summary-$(date +\%F).log 2>&1
```

### 3. Logrotate для `evolution/runs/`

`deploy/logrotate.d/edx-evolve`:

```
/opt/edx/evolution/runs/*/pipeline.log
/opt/edx/evolution/runs/*/pipeline.log.errors
/opt/edx/evolution/runs/*/claude.jsonl
{
    weekly
    rotate 4
    compress
    missingok
    notifempty
    nocreate
}
```

И отдельный housekeeping CLI: `edx evolve cleanup --older-than 30d` —
удаляет каталоги `evolution/runs/<N>/` старше указанного срока. По
умолчанию хранится 30 дней. Дополнительно `--keep-failed` оставляет
тики с verdict ∈ {fail, regression*} нетронутыми (полезно для
инвестигаций).

```python
# в src/edx/evolve/cleanup.py
def purge_old_runs(
    runs_dir: Path = Path("evolution/runs"),
    older_than: timedelta = timedelta(days=30),
    keep_failed: bool = False,
    repo: EvolutionRepo | None = None,
) -> tuple[int, int]:
    """Returns (removed_count, kept_count)."""
```

CLI subcommand:
```
edx evolve cleanup --older-than 30d [--keep-failed]
```

### 4. SLO-smoke тесты

`tests/evolve/test_slo_smoke.py` — высокоуровневый health-check,
который можно запускать на проде раз в день / cron'ом:

- `test_state_db_evolution_tables_present`: на боевой `state.sqlite`
  есть таблицы `evolution_ticks`, `evolution_skiplist`.
- `test_memory_md_present_and_parseable`: `evolution/MEMORY.md` парсится
  `memory.read()` без exception.
- `test_canary_baseline_present`: `data/canary_baseline.json` существует
  и не старше 30 дней.
- `test_settings_evolve_safe`: deny-list содержит обязательные правила.

Добавить новый make target:
```makefile
slo-smoke:
	$(PY) -m pytest tests/evolve/test_slo_smoke.py -q
```

### 5. USER_GUIDE.md — финальный Operator runbook

Обновляем существующий раздел из Patch 44 ещё несколькими сценариями:

```markdown
### Incident: ежедневный budget исчерпан слишком рано

Симптом: `edx evolve report` показывает `daily_cost ≥ 0.9 * cap`
до 18:00 МСК.

Действия:
1. `journalctl -u edx-evolve.service --since "today" | grep cost`
2. Найти выброс — обычно один тик с `cost > $5`.
3. Если тик прошёл (`verdict=ok`) — оставить, всё в порядке.
4. Если тик повторяется и не сходится — temp-disable до утра:
   ```bash
   sudo $EDITOR /opt/edx/.env.evolve   # EDX_EVOLVE_AGENT_ENABLED=0
   sudo systemctl restart edx-evolve.timer
   ```
   Утром следующего дня — debug bundle, вернуть =1.

### Incident: master сломан после auto-merge

Симптом: `make test` упал после `evolve(N): …` коммита.

Действия:
1. `git revert <sha> -m "manual revert: see issue #…"`
2. `git push origin master`
3. `edx evolve memory verify` — проверить, что упоминаемые в MEMORY.md
   файлы ещё на месте.
4. Завести issue с `evolution/runs/<N>/` приложенным.
5. (Опционально) `edx evolve reset --company-id <ID>` — снять с
   skiplist, чтобы попробовать снова.

### Incident: skiplist > 25% компаний

Симптом: `edx evolve report` пишет skiplist size > 30 of 125.

Это означает, что Claude Code не справляется с большим хвостом
кейсов. Действия:
1. `edx evolve memory show | grep "anti-pattern"` — может быть, мы
   слишком часто вето-блокируем фикс.
2. Реальное решение — оператор-патч (как обычные патчи серии 38-37).
3. После релиза патча — `edx evolve reset --company-id ID` для
   тех 30 компаний, которые имеет смысл попробовать заново.
```

### 6. Тесты-валидаторы документации

`tests/test_runbook_present.py`:
- USER_GUIDE.md содержит подзаголовки «Incident: ежедневный budget…»,
  «Incident: master сломан…», «Incident: skiplist >».
- README.md упоминает `deploy/PRODUCTION_ENABLE.md`.

## Acceptance criteria

- `make slo-smoke` ✅ на VPS (после rollout).
- `git log --grep='evolve(' master --since '7 days ago' | wc -l` > 0 в
  любой 7-дневный window после rollout.
- `edx evolve cleanup --older-than 60d` отработал на VPS, удалил старые
  bundle'ы.
- `evolution/MEMORY.md` за месяц вырос на ≥ 5 записей; ни одной
  записи без anti-regression поля.
- 0 ручных revert'ов commit'ов автоэволюции за неделю rollout'а.

## Риски и инварианты

- НЕ удаляем bundle'ы тиков, пока их `claude.jsonl` не обработан в
  отчёт. cleanup завязан на `started_at` тика — никаких живых ссылок.
- logrotate НЕ удаляет `failure_taxonomy.json` / `state-slice.sql` —
  только тяжёлые лог-файлы.
- `cleanup --older-than` минимум 7 дней — никаких truncate-now.
- PRODUCTION_ENABLE.md обязательно подписан оператором — без подписи
  systemd timer не должен ничего автоматически менять.

## Что класть в MEMORY.md

К моменту Patch 46 в MEMORY.md уже должно быть несколько реальных
записей. На этом этапе — только проверка структуры. Если оператор
видит, что 2+ запись в Anti-patterns противоречат друг другу
(«всегда расширять регэксп» vs «никогда не расширять без unit-теста»)
— это сигнал на consolidation через `edx evolve memory compact`.

---

## Финальная картина после Patch 46

```
edx-evolve.timer (systemd)  каждые 5 мин
       │
       ▼
edx evolve tick
   pick batch ── synth ── baseline run ── verdict
                                     │
                                     ▼
                              all OK? ── merge xlsx ── done
                                     │
                                     no
                                     ▼
                              build bundle (per-company)
                                     │
                                     ▼
                              read MEMORY.md ─── claude code (-p)
                                     │           │
                                     │           ▼
                                     │      modify code
                                     │           │
                                     │           ▼
                                     │      update MEMORY.md
                                     │           │
                                     ▼           ▼
                              tests + canaries + batch + memory gate
                                                 │
                                ┌────────────────┴───────────────┐
                                ▼                                ▼
                         git ff merge → master          git branch -D evolve/tick-N
                         git push origin                bump skiplist
                         done                           done
```

Всё, цикл закрыт. Дальше — operator monitoring и периодические
manual-патчи серии 47+ для архитектурных улучшений, к которым
автоэволюция не способна.
