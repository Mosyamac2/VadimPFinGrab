# Промпт 44. Самоэволюция: deploy на удалённом сервере (Claude Code, systemd, env)

> Зависимости: Patch 38–43. С этим патчем self-evolve можно физически
> запустить на VPS, но `EDX_EVOLVE_AGENT_ENABLED=1` пока выставляется
> вручную в `.env.evolve`. Включение по таймеру с реальной автономией
> — Patch 46.

## Цель

1. Скрипт `deploy/install_claude_code.sh` ставит Node 20+ и Claude Code
   CLI на хост-системе (Debian/Ubuntu), идемпотентно.
2. systemd units `edx-evolve.service` + `edx-evolve.timer`: каждые
   5 минут запускают `edx evolve tick` под flock'ом.
3. Шаблон `deploy/env.evolve.example` со всеми нужными переменными
   и пояснениями.
4. Раздел «Self-Evolve» в `README.md` с install-инструкцией и FAQ.
5. Раздел в `USER_GUIDE.md` про мониторинг и runbook.

## Контекст

- Существующая deploy-структура:
  ```
  deploy/
    cron/edx.crontab
    systemd/edx-update.service
    systemd/edx-update.timer
  ```
  `edx-update.timer` крутит обычный update раз в сутки. evolve-таймер
  должен сосуществовать с ним — flock на одном файле.
- `claude` CLI: `@anthropic-ai/claude-code` через npm. Требует Node 20+.
  Авторизация двумя способами:
  1. интерактивный `claude /login` (OAuth, кладёт токен в `~/.claude`).
  2. environment `CLAUDE_CODE_OAUTH_TOKEN=…`.
  Headless-режим использует второй — токен попадает через
  `EnvironmentFile`.
- Установочный путь VPS: `/opt/edx`. Юзер: `edx`.

## Задачи

### 1. `deploy/install_claude_code.sh`

```bash
#!/usr/bin/env bash
# Patch 44: install Node 20+ and @anthropic-ai/claude-code on a Debian/Ubuntu host.
# Idempotent: re-running the script is safe.

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

NODE_TARGET_MAJOR=20

current_node_major() {
  if ! command -v node >/dev/null; then echo 0; return; fi
  node -v | sed 's/^v//' | cut -d. -f1
}

if [[ "$(current_node_major)" -lt "${NODE_TARGET_MAJOR}" ]]; then
  echo "Installing Node.js ${NODE_TARGET_MAJOR}.x via NodeSource…"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_TARGET_MAJOR}.x" | bash -
  apt-get install -y nodejs
else
  echo "Node.js $(node -v) already installed."
fi

if ! command -v claude >/dev/null; then
  echo "Installing @anthropic-ai/claude-code globally…"
  npm install -g @anthropic-ai/claude-code
else
  echo "claude $(claude --version || echo unknown) already installed."
fi

echo "Done."
echo
echo "Next steps:"
echo "  1. As the edx user (sudo -iu edx) run 'claude /login' once interactively"
echo "     to drop a refresh token into ~/.claude. OR set CLAUDE_CODE_OAUTH_TOKEN"
echo "     in /opt/edx/.env.evolve directly."
echo "  2. Copy deploy/env.evolve.example to /opt/edx/.env.evolve and fill in."
echo "  3. Copy deploy/systemd/edx-evolve.{service,timer} to /etc/systemd/system/."
echo "  4. systemctl daemon-reload && systemctl enable --now edx-evolve.timer"
```

Делаем `chmod +x deploy/install_claude_code.sh` и коммитим
исполняемым (git mode 0755).

### 2. `deploy/systemd/edx-evolve.service`

```ini
# Patch 44: self-evolve tick runner. Triggered every 5 minutes by
# edx-evolve.timer. Serialised against itself AND against the daily
# edx-update.service via flock on /tmp/edx-evolve.lock.
#
# Failures of the underlying ETL (per-company) do NOT make the
# service unit fail — only crashes (non-zero exit + traceback) do.
# This is intentional: systemd shouldn't go red because of a normal
# failed tick.

[Unit]
Description=edx self-evolve tick (one batch of 3 companies)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/edx
EnvironmentFile=-/opt/edx/.env
EnvironmentFile=-/opt/edx/.env.evolve
ExecStart=/usr/bin/flock -n /tmp/edx-evolve.lock /opt/edx/.venv/bin/edx evolve tick
User=edx
Group=edx
TimeoutStartSec=75min
# Allow Claude Code's npm-installed binary on PATH:
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
Nice=10
IOSchedulingClass=idle

[Install]
WantedBy=multi-user.target
```

### 3. `deploy/systemd/edx-evolve.timer`

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
Persistent=false   # missed ticks are NOT replayed; they're cheap to drop

[Install]
WantedBy=timers.target
```

### 4. `deploy/env.evolve.example`

```dotenv
# Patch 44: environment file for the self-evolve loop.
# Copy to /opt/edx/.env.evolve (chmod 600, owner edx:edx).
#
# DO NOT commit a filled-in copy. NEVER share these tokens.

# --- Required ---
# Bearer token for Claude Code in headless mode.
# Obtain via: sudo -iu edx claude /login   (then read from ~/.claude/...).
# Alternatively the same value as in `claude config get auth.token`.
CLAUDE_CODE_OAUTH_TOKEN=

# Master switch. Until you flip this to 1 the loop runs in dry-run mode:
# pipeline executes, bundle is assembled, but no agent call and no commit.
# Recommended: leave =0 for one full day after install, inspect bundles,
# then flip to 1.
EDX_EVOLVE_AGENT_ENABLED=0

# --- Budgets ---
EDX_EVOLVE_DAILY_BUDGET_USD=25
EDX_EVOLVE_TICK_BUDGET_USD=2

# --- Tuning (sane defaults; touch only if you know what you're doing) ---
# EDX_EVOLVE_BATCH_SIZE=3
# EDX_EVOLVE_COOLDOWN_DAYS=7
# EDX_EVOLVE_MAX_TURNS=25
# EDX_EVOLVE_PIPELINE_TIMEOUT_S=1800
# EDX_EVOLVE_AGENT_TIMEOUT_S=1800
# EDX_EVOLVE_MERGE_XLSX=0
```

### 5. README.md — новый раздел

После раздела «10. Что НЕ входит в scope первой версии» добавить:

```markdown
## 11. Self-Evolution loop

Опциональный фоновой режим, в котором проект сам прогоняется на
~125 компаниях из `e-disclosure-companies.csv` и **сам же дописывает
свой код** через headless Claude Code, когда падает на новой
терминологии / разметке. Архитектура и инварианты —
[`PLAN_self_evolution.md`](PLAN_self_evolution.md). Журнал решённых
кейсов — [`evolution/MEMORY.md`](evolution/MEMORY.md).

### Установка (раз)

```bash
# 1. Node + Claude Code
sudo bash deploy/install_claude_code.sh

# 2. Получить refresh-token
sudo -iu edx claude /login          # interactive раз, токен в ~/.claude

# 3. Прокинуть env
sudo cp deploy/env.evolve.example /opt/edx/.env.evolve
sudo chown edx:edx /opt/edx/.env.evolve
sudo chmod 600 /opt/edx/.env.evolve
sudo $EDITOR /opt/edx/.env.evolve   # вписать CLAUDE_CODE_OAUTH_TOKEN

# 4. Канареечный baseline (первый запуск)
sudo -iu edx /opt/edx/.venv/bin/edx evolve canary capture

# 5. Установить таймер
sudo cp deploy/systemd/edx-evolve.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now edx-evolve.timer
```

После шага 5 каждые 5 минут запускается `edx evolve tick` в **dry-run**
(`EDX_EVOLVE_AGENT_ENABLED=0`). Понаблюдайте за `evolution/runs/` день,
затем во время рабочих часов оператора:

```bash
sudo $EDITOR /opt/edx/.env.evolve     # EDX_EVOLVE_AGENT_ENABLED=1
sudo systemctl restart edx-evolve.timer
```

С этого момента — full self-evolve. Все автокоммиты идут в master с
префиксом `evolve(N):`. Откат любого автокоммита: `git revert <sha>`.

### Мониторинг

| Команда | Что показывает |
|---|---|
| `edx evolve status --limit 20` | последние тики, verdict, cost |
| `edx evolve report` | агрегаты за 7 дней + бюджет |
| `cat evolution/MEMORY.md` | live-журнал решённых проблем |
| `journalctl -u edx-evolve.service -f` | systemd выхлоп |
| `evolution/runs/{N}/SUMMARY.md` | per-tick отчёт агента |
```

### 6. USER_GUIDE.md — runbook

Новый раздел «Self-Evolve runbook» с тремя сценариями:

1. **Откатить плохой авто-коммит** (regression обнаружили после
   merge'а):
   ```bash
   git revert <sha>
   git push origin master
   # NB: следующий тик увидит revert через memory_verify (Patch 43)
   # и пометит соответствующую запись MEMORY.md как stale.
   ```
2. **Снять компанию с skiplist'а:**
   ```bash
   .venv/bin/edx evolve reset --company-id 38588
   ```
3. **Полный сброс при подозрении на повреждённую память/баклог:**
   ```bash
   sudo systemctl stop edx-evolve.timer
   .venv/bin/edx evolve memory compact   # пред-просмотр
   # отредактировать evolution/MEMORY.md руками если нужно
   sudo systemctl start edx-evolve.timer
   ```

### 7. Тесты

`tests/deploy/test_systemd_units.py` — проверки текста файлов:
- `test_service_uses_flock`: содержит `flock -n /tmp/edx-evolve.lock`.
- `test_service_user_edx`: `User=edx`, `Group=edx`.
- `test_service_loads_env_evolve`: содержит обе строки
  `EnvironmentFile=…`.
- `test_timer_5min_cadence`: `OnUnitActiveSec=5min`.
- `test_timer_persistent_false`: missed ticks не реплеятся.

`tests/deploy/test_install_script.py` — статический анализ:
- shebang `#!/usr/bin/env bash`, `set -euo pipefail`;
- содержит `setup_20.x`, `npm install -g @anthropic-ai/claude-code`;
- идемпотентность: ветка `if ! command -v claude` присутствует.

`tests/deploy/test_env_evolve_example.py`:
- содержит обе обязательные переменные `CLAUDE_CODE_OAUTH_TOKEN`,
  `EDX_EVOLVE_AGENT_ENABLED`;
- бюджеты заданы дефолтами;
- НЕ содержит реального secret'а (паттерны `sk-…`, `oauth_…`, длинных
  base64 — должны отсутствовать).

`tests/test_readme_sync.py` (расширить существующий, если есть; иначе
новый):
- README.md содержит подзаголовок `## 11. Self-Evolution loop`;
- упоминает `deploy/install_claude_code.sh`,
  `evolution/MEMORY.md`, `EDX_EVOLVE_AGENT_ENABLED`.

## Acceptance criteria

- `make lint` ✅ `make typecheck` ✅ `make test` ✅
- `bash -n deploy/install_claude_code.sh` (синтаксис ОК).
- `systemd-analyze verify deploy/systemd/edx-evolve.service` (требует
  systemd на хосте — на CI можно пропустить через `pytest.importorskip`).
- На VPS после `bash deploy/install_claude_code.sh` команда `claude
  --version` отрабатывает.
- README.md содержит раздел 11 и раздел 12+ перенумерован соответственно.

## Риски и инварианты

- НЕ запускаем `npm install -g` без sudo — скрипт явно проверяет root.
- timer-юнит не делает `Persistent=true` — иначе после длительного
  оффлайна повалит вал тиков и пробьёт budget.
- `Nice=10 IOSchedulingClass=idle` — чтобы evolve не воровал ресурсы у
  основного `edx-update.timer` (тот идёт раз в сутки и важнее).
- `.env.evolve` обязательно с `chmod 600`. Скрипт это явно говорит,
  но проверка в systemd не делается — risk operator missed.

## Что класть в MEMORY.md

Anti-pattern: «Не объединяйте `EnvironmentFile` для `edx-update` и
`edx-evolve` — они должны иметь РАЗНЫЕ token'ы (для биллинга), и сейчас
deploy/ это разделяет».

Anti-pattern: «Не выкатывайте `EDX_EVOLVE_AGENT_ENABLED=1` без
сделанного `edx evolve canary capture` — gate без baseline даёт
ложно-положительные срабатывания».
