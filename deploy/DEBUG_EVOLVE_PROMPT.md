# Self-Evolve loop — debug prompt для оператора

> Этот файл — **готовый брифинг для Claude Code** на VPS.
> Запускается оператором интерактивно или через `-p`.
> claude уже авторизован Max-подпиской (`CLAUDE_CODE_OAUTH_TOKEN` в
> `/opt/edx/.env.evolve`), git-remote настроен через PAT, так что
> commit + push в master из этой сессии работают штатно.
>
> Запуск:
> ```
> cd /opt/edx
> claude --permission-mode acceptEdits
> # внутри REPL:
> # /run    deploy/DEBUG_EVOLVE_PROMPT.md
> ```
> или одной командой:
> ```
> cd /opt/edx
> claude -p "$(cat deploy/DEBUG_EVOLVE_PROMPT.md)" \
>   --permission-mode acceptEdits --max-turns 30 --verbose
> ```

---

# Контекст

Ты — debug-агент для self-evolve loop'а проекта `e-disclosure-extractor`
(описание архитектуры в `PLAN_self_evolution.md` и
`evolution/MEMORY.md`). Сам loop сейчас сломан и ты должен починить.

## История уже исправленных багов (ОБЯЗАТЕЛЬНО прочти перед началом)

1. **Commit `a9c224f`** — `claude_runner.argv` забывал флаг `--verbose`,
   обязательный для `--print + stream-json`. Без него claude печатал
   stderr-ошибку и exit'ил с code 1, claude.jsonl пустой, cost=0,
   turns=0. **Исправлено.** Не удаляй `--verbose` из argv — это
   anti-regression в `evolution/MEMORY.md`.

2. **Commit `025dfbf`** — Picker исключал компанию из skiplist при
   первом же `bump_failure` (failure_count=1), без учёта
   GIVE_UP_THRESHOLD=3. 53 компании заблокировались навсегда.
   **Исправлено.** Picker теперь читает `EvolutionSkiplistEntry`
   полностью и применяет threshold.

## Текущий симптом (ради чего ты запущен)

После двух фиксов выше тики продолжают валиться, но ПО-ДРУГОМУ:

```
$ /opt/edx/.venv/bin/edx evolve status --limit 5
#  56  phase=failed  verdict=fail  cost=$0.000  turns=1   sha=—
        error: claude_run_error
#  55  phase=failed  verdict=fail  cost=$0.000  turns=1   sha=—
        error: claude_run_error
#  54  phase=failed  verdict=fail  cost=$0.000  turns=1   sha=—
…
```

Заметь: `turns=1` (раньше было 0) → claude **запускается, делает один
turn, потом падает**. `cost=0` — это норма для Max-подписки (она не
тратит USD), но `error: claude_run_error` означает что в финальном
`result`-event'е поле `is_error: true`.

## STEP 0 — собрать актуальные данные

Прочитай в порядке:

1. `evolution/MEMORY.md` — все anti-pattern'ы и история патчей.
2. `evolution/runs/56/manifest.json` и `evolution/runs/56/batch.json`
   — какие компании в этом тике.
3. `evolution/runs/56/claude.jsonl` — **главный артефакт**. Это
   полный stream-json диалога. Найди:
   - событие `type: system` (стартовое, должно быть)
   - первое `type: assistant` (что агент сказал)
   - событие `type: result` (там `is_error`, причина)
4. `evolution/runs/56/SUMMARY.md` — если файл есть, что агент
   доложил перед концом.
5. Если `claude.jsonl` тика #56 даёт неполную картину — проверь #55
   и #54 (события одинаковы по структуре).

Потом:

6. `src/edx/evolve/claude_runner.py` — `run_agent`, `_absorb_event`.
   Как формируется `claude_run_error`: смотри tick.py
   `_gate_check_after_agent` + `claude_runner._absorb_event` где
   ставится `is_error=True`.
7. `src/edx/evolve/tick.py` — `run_one_tick` целиком. Особенно блок
   после `claude_res = run_agent(...)` и логику `agent_error`.
8. `.claude/commands/edx-evolve-fix.md` — slash-command, который
   агент в тике пытается выполнить. Может быть, в STEP 0 (там список
   обязательных Read'ов) встречается путь, который не существует или
   запрещён.
9. `.claude/settings.evolve.json` — sandbox: `allow` / `deny`. Может,
   агент натыкается на `deny`-rule и репортит is_error.

## STEP 1 — воспроизведи провал руками (с выводом stderr)

Запусти ровно ту же команду, что использует обёртка:

```bash
cd /opt/edx
set -a; source /opt/edx/.env.evolve; set +a
claude -p "/edx-evolve-fix 56" \
  --output-format stream-json --verbose \
  --add-dir /opt/edx/evolution/runs/56 \
  --max-turns 5 --permission-mode acceptEdits \
  > /tmp/repro.out 2> /tmp/repro.err
echo "=== stderr ==="
cat /tmp/repro.err
echo "=== last 3 events ==="
tail -3 /tmp/repro.out | python3 -m json.tool 2>/dev/null || tail -3 /tmp/repro.out
```

Если в stderr — что-то конкретное (permission denied, unknown tool,
invalid argument), это и есть зацепка. Если stderr пустой и
claude выдал валидный stream-json с `is_error: true` — читай
content `result`-event'а: там обычно текстовое объяснение.

## STEP 2 — гипотезы (от вероятной к менее)

| # | Гипотеза | Как проверить | Если подтвердится — что делать |
|---|---|---|---|
| 1 | Slash-command `/edx-evolve-fix` встретил недоступный путь в STEP 0 (например, `evolution/runs/56/state-slice.json` отсутствует или claude думает что нет права read) | grep'ом убедиться, что все 7 файлов из STEP 0 slash-command'а реально существуют в `evolution/runs/56/` | Поправь slash-command чтобы missing-файлы не были fatal'ом, или поправь bundle.assemble чтобы создавать stub'ы |
| 2 | `acceptEdits` не разрешает `Bash(...)` команды из allowed-tools (frontmatter slash-command) — какой-то tool не на whitelist | посмотреть в claude.jsonl на `tool_use` события до error | расширить `allowed-tools` в `.claude/commands/edx-evolve-fix.md` под нужный shell-pattern |
| 3 | Max-подписка возвращает `is_error: true` если turns=max_turns достигнут (вместо чистого exit) | посмотреть `result.subtype` в claude.jsonl: `error_max_turns` vs `success` | в `claude_runner._absorb_event` различать `subtype` и не считать `error_max_turns` фейлом, если turns > 0 и был полезный output |
| 4 | Slash-command просит писать в `evolution/MEMORY.md` (STEP 4), но это вне `--add-dir` который указывает только bundle | проверить что cwd /opt/edx и MEMORY.md видится агенту | ничего, должно работать; иначе — `--add-dir /opt/edx` тоже добавить |
| 5 | Агент пробует `git diff --stat`, что разрешено slash-command'ом, но в режиме оn-the-fly запускается из другой cwd → файлы не находятся | проверить `system`-event'а cwd | в обёртке передавать `cwd=Path("/opt/edx")` явно (вроде уже сделано) |

## STEP 3 — починка

Когда нашёл root cause:

1. Внеси **минимальное** изменение в код / slash-command / settings.
2. Запусти `make test` — должно быть зелёно.
3. Если фикс в коде Python — запусти `make lint` и `make typecheck`.
4. Прогони ещё одну ручную репродукцию из STEP 1, чтобы убедиться,
   что provoking-условие теперь не воспроизводится.
5. Добавь sentinel-тест в `tests/evolve/` (anti-regression — если
   баг повторится, тест словит).
6. Обнови `evolution/MEMORY.md` — новый раздел в `## Anti-patterns`
   с описанием бага, симптома и инвариантом, который ты ввёл.

## STEP 4 — commit + push

```bash
cd /opt/edx
git status
git add src/edx/ tests/evolve/ evolution/MEMORY.md \
        .claude/commands/edx-evolve-fix.md .claude/settings.evolve.json \
        2>/dev/null  # некоторые могут не быть тронуты
git commit -m "fix(evolve): debug-session — <root cause>

<one-paragraph explanation>

Co-Authored-By: Claude Opus (debug session) <noreply@anthropic.com>"
git push origin master
```

Не пуш, если `make test` red. Не пуш ничего из `deploy/`, `.env*`,
`evolution/runs/*`, `data/*`, `output/*`, `logs/*`,
`config-evolve/*`.

## STEP 5 — финальная проверка

Дождись следующего тика (~5 минут после push'а — надо чтобы systemd
дёрнул свежий код):

```bash
sleep 320
/opt/edx/.venv/bin/edx evolve status --limit 3
```

Ожидание: хотя бы один тик `verdict=ok` или `neutral`, **без**
`error: claude_run_error`. Если повторно `claude_run_error` — STEP 0
заново; не зацикливайся, после двух итераций debug'а отчитайся
оператору на чём остановился (через `say`-message в conversation).

## Граничные правила (HARD CONSTRAINTS)

- НЕ трогай `.env*`, `deploy/install_claude_code.sh`,
  `deploy/systemd/`, `data/state.sqlite*`, `evolution/runs/`.
- НЕ запускай `git push --force`, `git reset --hard`.
- НЕ останавливай `edx-evolve.timer` (`systemctl stop ...`) —
  пусть продолжает крутиться, фейл-тики не страшны, важно знать что
  меняется.
- НЕ редактируй `evolution/runs/<N>/` файлы (это immutable артефакты
  тиков).
- НЕ добавляй новых top-level зависимостей в `pyproject.toml` без
  крайней необходимости.

## Когда закончишь — отчёт

Распечатай в финальном сообщении:

- root cause одной фразой
- что ты изменил (файлы + краткое summary)
- результат `make test` / `make lint` / `make typecheck`
- результат повторной репродукции (короткий tail из stream-json)
- результат финальной проверки `edx evolve status` через 5 мин
- ссылка на свой коммит (`git log -1 --oneline`)
