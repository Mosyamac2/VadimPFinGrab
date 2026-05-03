# Production rollout — Self-Evolve (Patch 46 checklist)

> Заполняется и подписывается оператором перед тем, как переключить
> ``EDX_EVOLVE_AGENT_ENABLED=1`` на боевом VPS. До подписи и заполненной
> ссылки ни один auto-merge в master не должен происходить.

Дата: ____  
Оператор: ____  
VPS host: ____  
Anthropic billing account: ____

## Pre-flight (заполнить ДО включения)

- [ ] Pilot закрыт по чек-листу `deploy/PILOT_REPORT_template.md`
      (ссылка / сохранённая копия): _____
- [ ] `edx evolve report` за последнюю неделю dry-run: ≥ 5 OK тиков.
- [ ] Median agent cost ≤ `EDX_EVOLVE_TICK_BUDGET_USD * 0.75`.
- [ ] Daily peak ≤ `EDX_EVOLVE_DAILY_BUDGET_USD * 0.6`.
- [ ] `evolution/MEMORY.md` содержит ≥ 5 patches log entries
      (в пилоте + анти-паттерны до пилота).
- [ ] Skiplist (`edx evolve status`) ≤ 10% от общего числа компаний.
- [ ] `edx evolve canary capture` повторён в течение 24 ч до этой
      процедуры (актуальный baseline после всех pilot-изменений).
- [ ] `git log --grep='evolve(' master | wc -l` ≥ 5.
- [ ] Бэкап `data/state.sqlite` сохранён вне репозитория (snapshot путь): _____
- [ ] Бэкап `evolution/MEMORY.md` сохранён (snapshot путь): _____
- [ ] `make slo-smoke` зелёный на VPS.

## Cutover

Выполнять последовательно (не пропускать шаги):

1. ```sudo $EDITOR /opt/edx/.env.evolve```
   - изменить `EDX_EVOLVE_AGENT_ENABLED=0` → `EDX_EVOLVE_AGENT_ENABLED=1`
2. ```sudo systemctl restart edx-evolve.timer```
3. ```journalctl -u edx-evolve.service -f``` (наблюдать первые 1-2
   тика; ожидаемое поведение: pick batch → run → если fail → агент
   запускается с реальным cost'ом → gate → либо merge'ится в master
   с префиксом `evolve(N):`, либо abandon_branch без следов).
4. После первого OK-merge'а оператор смотрит `git log -1 master` —
   убедиться, что commit с агентским сообщением и whitelist'ом
   путей (ничего из `deploy/`, `.env`, `.git/`, `.claude/`).

## Post-rollout 24h

- [ ] `edx evolve report` показывает: ticks=N, ok≥1, daily_cost ≤ cap.
- [ ] Канарейки SBER/LKOH/IZNM в `edx evolve report` — ни одной с
      verdict=`regression_canary`.
- [ ] `make test` на master зелёный.
- [ ] 0 ручных revert'ов автокоммитов (если есть — запиши SHA): _____

## Sign-off

Оператор подписывает после ≥24 ч стабильной работы.

```
Подпись: ____    Дата: ____
```

---

## Откат (если что-то пошло не так)

Любой шаг в порядке `1 → 2 → 3` восстанавливает безопасное состояние:

1. **Выключить агента (быстро):**
   ```bash
   sudo $EDITOR /opt/edx/.env.evolve   # EDX_EVOLVE_AGENT_ENABLED=0
   sudo systemctl restart edx-evolve.timer
   ```
2. **Откатить плохой автокоммит:**
   ```bash
   git revert <bad-sha>
   git push origin master
   ```
3. **Полная остановка demon'а:**
   ```bash
   sudo systemctl stop edx-evolve.timer
   sudo systemctl disable edx-evolve.timer
   ```

Memory check после revert'а:
```bash
edx evolve memory verify
```
Записи MEMORY.md, ссылающиеся на удалённые revert'ом файлы, будут
помечены — оператор обновит вручную или дождётся следующего тика.
