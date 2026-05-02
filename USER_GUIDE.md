# Руководство оператора edx

Короткая практическая инструкция: что арендовать, какие ключи добыть и
куда положить, как запустить и где смотреть результат.

---

## Что вы получите

Один Excel-файл (`e-disclosure.xlsx`) на Google Drive с финансовыми
показателями + сообщениями о существенных фактах, обновляется автоматически
раз в сутки. Ссылка на файл не меняется между запусками.

---

## Шаг 1. Арендовать сервер

| Параметр | Минимум | Комфортно |
|---|---|---|
| ОС | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 ГБ | 8 ГБ |
| Диск | 50 ГБ SSD | 100 ГБ SSD |
| Доступ | root по SSH | + публичный IP не обязателен |

Провайдеры из РФ (без проблем со связью с e-disclosure.ru): **Selectel**,
**Timeweb Cloud**, **Yandex Cloud**, **Reg.ru**. Стоимость ≈ 500–1500 ₽/мес.

Зарубежные (Hetzner, Vultr) — могут не открывать e-disclosure.ru
из-за блокировок; используйте только если уверены в маршрутизации.

---

## Шаг 2. Установить системные пакеты

Подключитесь по SSH и выполните:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils
```

> **Опционально:** `sudo apt install -y unrar` — нужен только если на
> e-disclosure внезапно появятся RAR-архивы; в текущих публикациях их нет,
> ZIP-публикации проходят без `unrar`.

> **Какой Python:** проекту нужен ≥ 3.11. Ubuntu **24.04** уже идёт с Python
> 3.12 в `python3`, и команда выше всё ставит. На Ubuntu **22.04** в
> `python3` стоит 3.10 — он слишком старый. Тогда:
> ```bash
> sudo add-apt-repository ppa:deadsnakes/ppa
> sudo apt update
> sudo apt install -y python3.11 python3.11-venv
> ```
> и в Шаге 3 запускайте `python3.11 -m venv .venv` вместо `python3 -m venv .venv`.

Проверка:

```bash
python3 --version           # 3.11.x или 3.12.x
tesseract --list-langs      # должны быть rus и eng
# unrar --version           # только если ставили опционально
```

Если `python3 --version` выдал 3.10 или ниже — переустановите через
deadsnakes PPA как в подсказке выше.

---

## Шаг 3. Скачать и установить проект

```bash
sudo mkdir -p /opt/edx
sudo chown $USER:$USER /opt/edx
cd /opt/edx
git clone https://github.com/Mosyamac2/VadimPFinGrab.git .
python3 -m venv .venv          # на 22.04 — python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Проверка:

```bash
edx --help
```

Должна появиться справка со списком команд.

---

## Шаг 4. Получить API-ключи

### 4.1 Anthropic (основной LLM)

1. https://console.anthropic.com/ → войти.
2. Привязать карту, пополнить баланс (хватит $10 на старт).
3. **API Keys** → **Create Key** → скопировать ключ `sk-ant-api03-...`.

### 4.2 OpenRouter (резервный LLM)

1. https://openrouter.ai/ → **Sign In** (через Google).
2. **Settings → Keys → Create Key** → скопировать `sk-or-v1-...`.
3. Пополнить баланс ($5 хватит).

> Можно использовать только один из двух — пайплайн упадёт, только если
> оба ключа отсутствуют. Лучше иметь оба: Anthropic — приоритет (поддерживает
> нативный PDF-input), OpenRouter — fallback.

### 4.3 Google Drive (Client ID + Client Secret)

1. https://console.cloud.google.com/ → создать новый проект `edx-pipeline`.
2. **APIs & Services → Library** → найти **Google Drive API** → **Enable**.
3. **APIs & Services → OAuth consent screen** →
   - User Type: **External** → Create.
   - App name: `edx`. Support email: ваш email.
   - Scopes: оставить по умолчанию.
   - Test users: добавить ваш Gmail.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   - Application type: **Desktop app**. Name: `edx-cli`.
   - Скачать JSON или скопировать `client_id` (`...apps.googleusercontent.com`)
     и `client_secret` (`GOCSPX-...`).

### 4.4 Refresh-token Google Drive

Получаем после Шага 6 командой `edx auth google-drive` — токен прилетит в
терминал.

---

## Шаг 5. Заполнить `.env`

```bash
cd /opt/edx
cp .env.example .env
chmod 600 .env
nano .env
```

Пример заполненного файла:

```ini
ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXXXXXXXXXXXXXXXXX
OPENROUTER_API_KEY=sk-or-v1-XXXXXXXXXXXXXXXXXXXXXXXXXX

GOOGLE_OAUTH_CLIENT_ID=12345-abc.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-zzzzzzzzzzzzzzzzzz
GOOGLE_OAUTH_REFRESH_TOKEN=          # пока пусто, заполним в Шаге 6

YANDEX_VISION_OCR_KEY=               # не нужен в первой версии
```

Сохранить: `Ctrl+O`, `Enter`, `Ctrl+X`.

---

## Шаг 6. Авторизация Google Drive

> **Важно про SSH/headless:** OAuth-флоу нужен браузер. На голом VPS его нет,
> и `edx auth google-drive` напрямую через SSH **не сработает**: команда
> поднимает локальный HTTP-сервер на сервере и ждёт redirect — но открыть
> Google-консент в браузере на удалённой машине нечем.
>
> Используйте один из вариантов ниже.

### Вариант A (рекомендуемый) — авторизация на своём ноутбуке

`refresh_token` привязан к `client_id`/`client_secret`, а не к машине. Получим
его локально, перенесём строкой на сервер.

**На своём ноутбуке** (Windows / macOS / Linux — главное наличие браузера):

1. Установить Python 3.11+ и `google-auth-oauthlib`:
   ```bash
   pip install google-auth-oauthlib
   ```
2. Скачать одноразовый скрипт из репозитория:
   ```bash
   curl -O https://raw.githubusercontent.com/Mosyamac2/VadimPFinGrab/master/tools/get_drive_token.py
   ```
   (или склонировать репозиторий целиком и взять `tools/get_drive_token.py`).
3. Запустить, передав те же `CLIENT_ID` и `CLIENT_SECRET`, что лежат в `.env`
   на сервере:
   ```bash
   python get_drive_token.py "12345-abc.apps.googleusercontent.com" "GOCSPX-xxxxx"
   ```
4. Откроется браузер → залогиниться тестовым Gmail → принять запрос.
5. В терминал напечатается `GOOGLE_OAUTH_REFRESH_TOKEN` — длинная строка
   вида `1//0gAAAA...`.
6. Скопировать её в **серверный** `.env`:
   ```bash
   ssh user@server
   cd /opt/edx
   nano .env
   # вписать GOOGLE_OAUTH_REFRESH_TOKEN=1//0gAAAA...
   ```

### Вариант B — SSH-туннель (для тех, кто не хочет ставить Python локально)

На своём ноуте открыть SSH с проброшенным портом (8765 — любой свободный):

```bash
ssh -L 8765:localhost:8765 user@server
```

Внутри SSH-сессии запустить `edx auth google-drive`. Команда напечатает URL
вида `https://accounts.google.com/o/oauth2/auth?...&redirect_uri=http://localhost:RANDOM`.
**Замените `RANDOM` на `8765` в URL** перед открытием в браузере ноутбука.
После авторизации Google перенаправит на `http://localhost:8765/?code=...`,
запрос пройдёт через SSH-туннель на сервер, и токен будет получен.

Этот способ требует, чтобы случайный порт совпал с проброшенным —
проще запустить Вариант A.

### После получения токена

```bash
edx config check       # должен напечатать google_oauth_refresh_token: '***'
```

Это разовая операция; токен не истекает, пока вы не отзовёте доступ на
https://myaccount.google.com/permissions.

---

## Шаг 7. Папка на Google Drive

1. Откройте https://drive.google.com/ → **+ Создать → Папка** → название
   на ваш вкус (например, `edx-mart`).
2. Зайдите в папку, скопируйте **ID из URL**: часть после `/folders/`.
   Пример: URL `https://drive.google.com/drive/folders/1A2b3C4d5E6f` →
   ID = `1A2b3C4d5E6f`.
3. Откройте `config/app.yaml` и пропишите:

```yaml
google_drive:
  enabled: true              # было false
  folder_id: 1A2b3C4d5E6f    # ваш ID
  file_name: e-disclosure.xlsx
  archive: false             # true — если хотите датированные снапшоты
```

---

## Шаг 8. Перечень эмитентов

Откройте `config/tickers.yaml`. В репозитории идёт scaffold на 51 эмитента
из MOEX Top-50 — для **SBER (id=3043)** и **LKOH (id=17)** id уже
проставлены, остальные с пометкой `REPLACE_ME`. У каждого тикера также
указан `profile: bank | non_bank` — он выбирает набор метрик из
`config/metrics.yaml` (банкам идут `net_interest_income`, `net_fee_income`,
`total_equity`; корпоратам — `revenue`, `ebitda`, `total_debt`).

**Как найти e_disclosure_id**:

```bash
cd /opt/edx
source .venv/bin/activate
python tools/find_e_disclosure_ids.py --tickers VTBR,GAZP,ROSN
```

Скрипт ищет на e-disclosure-search по имени эмитента и печатает топ-3
кандидатов с `confidence`. Сам файл он не правит — выберите id руками
(у группы могут быть несколько юр.лиц с похожими названиями) и впишите
в `config/tickers.yaml`. Альтернатива: открыть
https://www.e-disclosure.ru/ → найти эмитента → URL вида
`…company.aspx?id=N` — число `N` и есть `e_disclosure_id`.

Пример заполненной записи (банк + корпорат):

```yaml
tickers:
  - ticker: SBER
    name: ПАО Сбербанк
    e_disclosure_id: "3043"
    profile: bank
    inn: "7707083893"

  - ticker: LKOH
    name: ПАО "ЛУКОЙЛ"
    e_disclosure_id: "17"
    profile: non_bank
```

**Перед первым `edx update`** прогоните скрипт-валидатор — он проверит,
что у каждого тикера хотя бы один из `type=3/4/5` отдаёт реальные
публикации:

```bash
python tools/validate_tickers.py --strict
```

`MISSING` для одного типа — нормальный кейс (пример: LKOH `id=17` не
публикует МСФО — для Лукойла работаем только с РСБУ). С `--strict` exit-code 1
встаёт только когда у тикера ни один из `type=3/4/5` не дал OK.

---

## Шаг 9. Первый запуск

```bash
edx config check     # проверка YAML + .env (секреты замаскированы)
edx update           # сам прогон пайплайна
edx status           # сводка по запуску
```

После успешного `edx update`:

- `/opt/edx/output/e-disclosure.xlsx` — локальный Excel.
- В Google Drive в вашей папке появится файл с тем же именем.
- `edx status` напечатает прямую ссылку на Google Drive.

При первом запуске пайплайн **скачивает отчётность за 3 года** — может
занять 30–60 минут и потратить 5–20$ на LLM в зависимости от количества
эмитентов.

---

## Шаг 10. Автозапуск раз в сутки

Самый простой вариант — `cron`:

```bash
crontab -e
```

Добавить строку (запуск в 04:00 каждый день):

```
0 4 * * * /opt/edx/.venv/bin/edx update >> /opt/edx/logs/cron.log 2>&1
```

Сохранить (`Ctrl+O`, `Enter`, `Ctrl+X`). Готово.

Альтернативно — systemd timer:

```bash
sudo cp /opt/edx/deploy/systemd/edx-update.service /etc/systemd/system/
sudo cp /opt/edx/deploy/systemd/edx-update.timer  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now edx-update.timer
sudo systemctl status edx-update.timer
```

---

## Полезные команды

| Команда | Что делает |
|---|---|
| `edx update` | Инкрементальный прогон (берёт только новое) |
| `edx run --full-reload` | Переобработать последние 3 года с нуля |
| `edx run --ticker SBER` | Прогнать только один эмитент |
| `edx status` | Последние 5 запусков + ссылка на Drive |
| `edx config check` | Проверить YAML и `.env` |
| `edx export-excel` | Только пересобрать Excel из текущего state |
| `edx replicate` | Только залить текущий Excel на Drive |
| `edx cache prune --older-than 30d` | Очистить старый LLM-кеш |

---

## Какие YAML можно править

| Файл | Что менять | Что НЕ трогать |
|---|---|---|
| `config/tickers.yaml` | Список эмитентов; поле `profile: bank \| non_bank` у каждого; `e_disclosure_id` через `find_e_disclosure_ids.py` | Структура полей |
| `config/metrics.yaml` | Внутри `profiles.bank` / `profiles.non_bank` — добавлять метрики и синонимы (каждый синоним аннотируется реальной фикстурой); `only_in_sources` / `aggregation_hint` для тонкой настройки | Структуру `profiles:` (ломающее изменение Patch 19); `reporting_priority` без необходимости |
| `config/event_types.yaml` | Новые типы событий | Запись с `code: other` (обязательна) |
| `config/app.yaml` | `schedule.cron_time`, `google_drive.*`, `validator.completeness_threshold`, `discoverer.requests_per_second` | Все `paths:` |
| `config/llm.yaml` | `cache_enabled`, `concurrency`, `max_retries` | `model` без причины |
| `config/ocr.yaml` | `tesseract_dpi` (300/400/600) | `engine` — пока поддерживается только `tesseract` |

### Что значат основные параметры

**`config/app.yaml`**

```yaml
schedule:
  cron_time: "04:00"            # время вашего ежедневного cron-запуска
  timezone: Europe/Moscow

mode:
  backfill_years: 3             # глубина истории при первом запуске и --full-reload

discoverer:
  requests_per_second: 1.0      # вежливость к e-disclosure.ru — не повышать без нужды
  respect_robots: true          # уважать robots.txt — оставьте true

downloader:
  concurrency: 4                # сколько публикаций качать параллельно

validator:
  completeness_threshold: 0.5   # ниже какой доли извлечённых метрик пометить публикацию incomplete

google_drive:
  enabled: true                 # переключатель репликации
  folder_id: 1A2b3C4d5E6f       # ID папки в Google Drive
  archive: false                # true — сохранять датированные снапшоты
```

**`config/llm.yaml`**

```yaml
primary:
  model: claude-sonnet-4-6      # модель Anthropic — менять не нужно
fallback:
  model: anthropic/claude-sonnet-4.6   # на OpenRouter
max_tokens: 4096
temperature: 0.0                # 0 = стабильные ответы, не повышать
cache_enabled: true             # сохранять ответы LLM на диск, экономит деньги
```

---

## Где что лежит

| Что | Путь |
|---|---|
| Excel-витрина (локально) | `/opt/edx/output/e-disclosure.xlsx` |
| JSON-логи | `/opt/edx/logs/pipeline.log` |
| Состояние БД | `/opt/edx/data/state.sqlite` (открывается в DBeaver / `sqlite3`) |
| Скачанные PDF/RAR | `/opt/edx/data/raw/{TICKER}/{PUB_ID}/` |
| LLM-кеш | `/opt/edx/data/processed/_llm_cache/` |
| Cron-лог | `/opt/edx/logs/cron.log` |

---

## Если что-то не работает

| Симптом | Решение |
|---|---|
| `No LLM providers configured` | Заполните `ANTHROPIC_API_KEY` или `OPENROUTER_API_KEY` в `.env` |
| `oauth_missing_credentials` | Заполните `GOOGLE_OAUTH_CLIENT_ID` + `GOOGLE_OAUTH_CLIENT_SECRET` в `.env` |
| `replication_no_folder_id` | Пропишите `google_drive.folder_id` в `config/app.yaml` |
| `discoverer_fetch_failed` для всех тикеров | Проверьте интернет на сервере; e-disclosure.ru может быть недоступен из вашего хостинга |
| `tesseract: command not found` | `sudo apt install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils` |
| `edx update` вернул `0 publications` | Tickers.yaml ещё с `REPLACE_ME` или id неверный. Прогоните `python tools/validate_tickers.py --strict` — он покажет, у какого тикера ни один тип не отвечает; для починки `python tools/find_e_disclosure_ids.py --tickers <X>`. |
| `edx update` отвечает 200 OK, но в логах сплошные `discoverer_no_publications_for_type`, body_bytes ≈ 1700 на каждый запрос | Это ServicePipe JS-challenge — `httpx` не проходит JA3-проверку. Решение: переключиться на Playwright-бэкенд (см. ниже «ServicePipe / headless Chromium»). |
| В Excel пустые `revenue`/`ebitda` для конкретного тикера | Проверьте поле `profile` в `tickers.yaml`. Для банков (SBER, VTBR, …) этих метрик и не должно быть — пайплайн собирает у них `net_interest_income`, `net_fee_income`, `total_equity`. Если ткер действительно корпорат и должен иметь revenue — поставьте `profile: non_bank`. |
| `discoverer_no_publications_for_type` для всех 4 типов одного тикера | Этот тикер либо не публикует ничего на e-disclosure, либо `e_disclosure_id` указывает на пустую карточку. Перепроверить через `find_e_disclosure_ids.py`. |
| Падает на `disk full` | `df -h /opt/edx`; почистить `data/raw/` старых публикаций или `edx cache prune --older-than 7d` |
| Excel не обновляется на Google Drive | Проверьте `google_drive.enabled: true` в `app.yaml`; `edx replicate` для ручного прогона |

Подробности первой ошибки всегда лежат в `logs/pipeline.log` (JSON).
Например, поиск свежих ошибок:

```bash
tail -n 200 /opt/edx/logs/pipeline.log | grep '"level": "error"'
```

---

## Стоимость в эксплуатации

| Статья | За месяц |
|---|---|
| VPS (2 vCPU / 4 GB) | 500–1500 ₽ |
| Anthropic API (LLM) | $5–30 в зависимости от количества эмитентов |
| OpenRouter (если используется) | сопоставимо |
| Google Drive | бесплатно (≤ 15 ГБ) |

---

## Безопасность

- Файл `.env` **не должен** попасть в Git (он в `.gitignore`).
- Установите `chmod 600 .env`.
- Любой ключ можно отозвать на стороне провайдера, если он скомпрометирован:
  Anthropic Console → API Keys → Revoke; OpenRouter → Settings → Keys → Delete;
  Google → https://myaccount.google.com/permissions → Remove access.
- `edx config check` всегда печатает секреты как `***` — можно копировать
  вывод в чат с поддержкой без риска.

---

## HTTPS-прокси для Anthropic / Google Drive (Patch 24)

Если ваш VPS не имеет прямого доступа к Anthropic API и Google Drive
(блокировки по странам, обычная история для российских VPS), а наружу
выходите через локальный туннель (vless / SOCKS / HTTP-прокси), то
нужно разделить трафик: LLM и Drive — через туннель, e-disclosure —
напрямую.

### 1. Поднимите прокси-клиент локально на VPS

Любой клиент vless/sing-box/xray, слушающий HTTP-прокси на
`127.0.0.1:10809` (или другом порту). Проверка:

```bash
ss -tlnp | grep 10809                             # должен быть LISTEN
curl -x http://127.0.0.1:10809 -i \
     https://api.anthropic.com/v1/messages | head -3
# ожидается HTTP/2 405 (Method Not Allowed без POST/auth) — значит
# до Anthropic долетели через туннель
```

### 2. Включите перенаправление через env-переменные

```bash
echo 'export HTTPS_PROXY=http://127.0.0.1:10809' >> ~/.bashrc
echo 'export HTTP_PROXY=http://127.0.0.1:10809'  >> ~/.bashrc
echo 'export NO_PROXY=e-disclosure.ru,www.e-disclosure.ru,localhost,127.0.0.1' >> ~/.bashrc
source ~/.bashrc
```

| Кому идём | Куда уходит | Почему |
|---|---|---|
| Anthropic SDK / OpenRouter | через прокси | `httpx` уважает `HTTPS_PROXY` + `NO_PROXY` |
| Google Drive API | через прокси | Patch 24 — `httplib2.proxy_info_from_environment` ловит env, `NO_PROXY` тоже |
| e-disclosure (Playwright Chromium) | напрямую | Chromium env-переменные не читает по умолчанию |
| e-disclosure (httpx-бэкенд, если когда-то переключите) | напрямую | благодаря `NO_PROXY=e-disclosure.ru` |

После старта `edx update` в логе появится строчка
`drive_proxy_configured` с заполненными `proxy_url` / `no_proxy` —
маркер, что Patch 24 подхватил env.

### 3. Cron / systemd

`~/.bashrc` cron не подхватывает. В юните:

```ini
[Service]
Environment="HTTPS_PROXY=http://127.0.0.1:10809"
Environment="HTTP_PROXY=http://127.0.0.1:10809"
Environment="NO_PROXY=e-disclosure.ru,www.e-disclosure.ru,localhost,127.0.0.1"
```

или через `EnvironmentFile=/etc/edx/proxy.env` (тот же ключ-значение
без `export`).

### Если всё равно `Connection refused` на Anthropic

- Проверьте, что `curl -x http://127.0.0.1:10809 https://api.anthropic.com/...`
  работает напрямую из той же шелл-сессии.
- Сравните выходной IP: `curl -x http://127.0.0.1:10809 https://ifconfig.me`
  должен **отличаться** от `curl https://ifconfig.me`. Если совпадают —
  vless-клиент маршрутизирует direct, а не туннелирует. Чините конфиг
  vless (outbounds / routing).
- Если выходной IP другой, но Anthropic всё равно блокирует — выходная
  нода тоже в стране-блокированной зоне. Смените сервер.

---

## ServicePipe / headless Chromium

e-disclosure стоит за анти-ботом ServicePipe. Он сверяет **TLS-fingerprint
(JA3)** клиента — обычный `httpx` (Python) делает рукопожатие иначе, чем
Chrome, и cookies, выданные Chrome'у, отвергаются «не-Chrome» клиенту.
Симптом: запросы возвращают 200 OK, но `body_bytes` ≈ 1700 (это
challenge-страница, не настоящая таблица), а в логах сплошные
`discoverer_no_publications_for_type`.

Лекарство — Playwright-бэкенд: запускает headless-Chromium один раз за
прогон, JS-challenge проходит **в браузере**, и все последующие
запросы Discoverer/Downloader идут через тот же Chromium-стек (JA3
совпадает, cookies стабильны).

### Установка

```bash
cd /opt/edx
source .venv/bin/activate
pip install '.[playwright]'           # сама библиотека
playwright install chromium           # скачивает браузер (~300 МБ)
playwright install-deps chromium      # доставляет системные libs (libnss3 и т.д.)
```

### Включение

В `config/app.yaml` под `discoverer:`:

```yaml
discoverer:
  ...
  http_backend: playwright             # было: httpx
```

После этого `edx update` использует Chromium. `discoverer.cookies` можно
оставить пустыми — Playwright соберёт свежие cookies сам при загрузке
бутстрап-страницы. Если на сервере есть оставшиеся cookies от ручных
экспериментов — они будут переданы как seed (не помешают).

### Стоимость

- ~250 МБ ОЗУ во время прогона (один Chromium процесс)
- ~3–5 секунд на инициализацию браузера + ~1–2 секунды на каждый запрос
  поверх обычного rate-limit (1 RPS). Для cron-прогона на 50 эмитентов
  это +5 минут к общему времени — приемлемо.
- Дисковое: ~300 МБ на сам Chromium (раз).

### Если всё ещё `discoverer_no_publications_for_type`

- Проверьте, что Chromium запустился: в логе должно быть
  `playwright_client_started` сразу после `migrations_up_to_date`.
- Иногда `playwright install-deps` пропускает что-то — попробуйте
  вручную:
  ```bash
  sudo apt install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
      libcups2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
      libgbm1 libpango-1.0-0 libcairo2 libasound2t64
  ```
- Если ServicePipe вернул 4xx даже Chromium'у — возможно временная
  блокировка по IP. Подождите 10–15 минут и попробуйте снова.

---

## Когда нужна помощь разработчика

Не настраивайте сами — обратитесь к разработчику в этих случаях:

- Хотите добавить **новый показатель** с нестандартным расчётом (формула,
  делитель, нормализация валют).
- Хотите подключить **облачный OCR** (Yandex Vision / Google Vision —
  сейчас они стоят как заглушки).
- Хотите **уведомления** в Telegram/email — это вне scope первой версии
  (см. `README.md` раздел 10).
- Нужен **REST API** или мобильное приложение — отдельный этап.
