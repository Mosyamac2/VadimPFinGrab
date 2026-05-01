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
    unrar tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils
```

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
unrar --version             # должен напечатать UNRAR ...
tesseract --list-langs      # должны быть rus и eng
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

```bash
edx auth google-drive
```

Команда:
1. Откроет в браузере ссылку для входа в ваш Google-аккаунт.
2. Разрешите приложению `edx` доступ к Drive.
3. В терминал напечатается длинная строка — это `refresh_token`.

Откройте `.env` и впишите её:

```ini
GOOGLE_OAUTH_REFRESH_TOKEN=1//0gAAAA...
```

Это разовая операция; токен не истекает, пока вы сами не отзовёте доступ.

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

Откройте `config/tickers.yaml`. Там 3 примера с `REPLACE_ME` в полях
`e_disclosure_id`. Заменить на реальные ID.

**Как найти e_disclosure_id**:
1. https://www.e-disclosure.ru/ → найти эмитента.
2. Открыть его карточку. URL вида `...company.aspx?id=26` — число `26`
   и есть `e_disclosure_id`.

Пример минимального файла:

```yaml
tickers:
  - ticker: SBER
    e_disclosure_id: "26"
    name: ПАО Сбербанк
    inn: "7707083893"

  - ticker: GAZP
    e_disclosure_id: "934"
    name: ПАО Газпром
```

Можно дописать любое количество.

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
| `config/tickers.yaml` | Список эмитентов | Структура полей |
| `config/metrics.yaml` | Можно добавлять метрики и синонимы | Уже существующие `canonical_name` (ломает совместимость с прошлыми запусками) |
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
| `unrar: command not found` | `sudo apt install unrar` |
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

## Когда нужна помощь разработчика

Не настраивайте сами — обратитесь к разработчику в этих случаях:

- Хотите добавить **новый показатель** с нестандартным расчётом (формула,
  делитель, нормализация валют).
- Хотите подключить **облачный OCR** (Yandex Vision / Google Vision —
  сейчас они стоят как заглушки).
- Хотите **уведомления** в Telegram/email — это вне scope первой версии
  (см. `README.md` раздел 10).
- Нужен **REST API** или мобильное приложение — отдельный этап.
