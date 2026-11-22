# gpc_bot

Telegram-бот: ссылка → пост в канал (картинка + название + ссылка).

## Файлы
- `bot.py` — логика бота (python-telegram-bot, long polling)
- `link_parser.py` — загрузка страницы, извлечение `og:title` / `og:image`
- `config.example.py` — шаблон настроек (`BOT_TOKEN`, `CHAT_ID`, `CHANNEL_ID`)
- `config_runtime.py` — чтение настроек: env-переменные имеют приоритет над `config.py`, поэтому на хостинге достаточно задать их в Environment Variables
- `whoami.py` — утилита: показывает `chat.id` чата/канала, где было сообщение
- `DEPLOY.md` — бесплатный деплой (Render и др.) и про «засыпание» free-планов
- `Dockerfile` — образ для Docker (arm64 для Raspberry Pi)
- `docker-compose.yml` — запуск через docker compose
- `requirements.txt`

## Быстрый старт локально
```bash
pip install -r requirements.txt
cp config.example.py config.py   # заполнить BOT_TOKEN, CHAT_ID, CHANNEL_ID
python bot.py
```
Три настройки = три переменные окружения (так же они задаются на хостинге):
```bash
export BOT_TOKEN="123:ABC..."
export CHAT_ID="-1003743298448"      # чат, откуда берём ссылки
export CHANNEL_ID="-1004447835579"   # канал, куда публикуем
python bot.py
```

## Деплой на Home Assistant OS (Raspberry Pi 4, arm64)

**Лучший способ — Docker Compose на хосте HAOS** (не аддон, не Portainer, просто `docker compose` в SSH).

### 1. Подготовка на HAOS
Включи **SSH-аддон** (Terminal & SSH) в HA: Настройки → Аддоны → Магазин → Terminal & SSH → Установить → Запустить → «Защита режима» выкл.

### 2. Клонирование и конфиг
```bash
# В SSH HAOS
cd /root
git clone https://github.com/Lowbeat/gpc_bot.git
cd gpc_bot

# Конфиг: env-переменные имеют приоритет над config.py (см. config_runtime.py)
# Создай .env файл (НЕ коммитится в git)
cat > .env << 'EOF'
BOT_TOKEN=1234567890:AAAA...
CHAT_ID=-1003743298448
CHANNEL_ID=-1004447835579
KEEP_ALIVE_MIN=10
EOF
```

### 3. Сборка и запуск
```bash
# Первый запуск — скачает образ и соберёт (2-5 мин на Pi 4)
docker compose up -d --build

# Логи
docker compose logs -f

# Остановка
docker compose down
```

### 4. Обновление бота
```bash
cd /root/gpc_bot
git pull
docker compose up -d --build  # пересоберёт только если есть изменения
```

### Важные нюансы
- **Память**: Pi 4 4 Гб — HA ~1.3 Гб + бот с Chromium ~0.3 Гб = запас есть.
- **Образ**: `Dockerfile` использует `mcr.microsoft.com/playwright/python:v1.65.2-jammy-arm64` — нативный arm64, Chromium внутри тянется для arm64.
- **Перезапуск**: `restart: unless-stopped` в compose переживёт ребут Pi.
- **Сеть**: исходящий трафик открыт, бот ходит в `api.telegram.org` и маркетплейсы.
- **Секреты**: только в `.env` (в `.gitignore`). `config.py` не нужен — `config_runtime.py` читает env.

## Как это работает
Бот постит **только когда его тегнут** — в любом чате, включая привязанный:
- `@bot_username https://...` в группе/чате → пост уходит в канал, привязанный к этому чату (`CHAT_CHANNEL_MAP`), иначе в `CHANNEL_ID`;
- реплай сообщения бота со ссылкой тоже считается тегом;
- в личке тегать не нужно — любая ссылка (и `/post <ссылка>`);
- сообщения без упоминания бота игнорируются полностью.

Важно: чтобы бот видел упоминания в группах, у него должен быть выключен Privacy Mode (`/setprivacy` → Disable) — иначе Telegram вообще не присылает ему обычные сообщения, и тег до бота не дойдёт.

## Настройка Telegram
1. Бот у @BotFather → токен.
2. Бот — **администратор канала** с правом публикации.
3. Бот — участник чата; для групп отключить Privacy Mode (`/setprivacy` → Disable).
4. ID вида `-100XXXXXXXXXX` узнаются через `python whoami.py` (напиши что-нибудь в чат/канал → увидишь `chat.id=`).
