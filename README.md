# gpc_bot

Telegram-бот: ссылка → пост в канал (картинка + название + ссылка).

## Файлы
- `bot.py` — логика бота (python-telegram-bot, long polling)
- `link_parser.py` — загрузка страницы, извлечение `og:title` / `og:image`
- `config.py.example` — шаблон настроек (`BOT_TOKEN`, `CHAT_ID`, `CHANNEL_ID`)
- `config_runtime.py` — чтение настроек: env-переменные имеют приоритет над
  `config.py`, поэтому на хостинге достаточно задать их в Environment Variables
- `whoami.py` — утилита: показывает `chat.id` чата/канала, где было сообщение
- `DEPLOY.md` — бесплатный деплой (Render и др.) и про «засыпание» free-планов
- `requirements.txt`

## Быстрый старт локально
```bash
pip install -r requirements.txt
cp config.py.example config.py   # заполнить BOT_TOKEN, CHAT_ID, CHANNEL_ID
python bot.py
```
Три настройки = три переменные окружения (так же они задаются на хостинге):
```bash
export BOT_TOKEN="123:ABC..."
export CHAT_ID="-1003743298448"      # чат, откуда берём ссылки
export CHANNEL_ID="-1004447835579"   # канал, куда публикуем
python bot.py
```
Несколько каналов сразу — одной переменной `CHAT_CHANNEL_MAP`
(формат `чат:канал,чат:канал`, см. DEPLOY.md).

## Как это работает
- В чате, привязанном к каналу (`CHAT_ID` или пара из `CHAT_CHANNEL_MAP`),
  бот публикует **любую ссылку** — тегать его не нужно.
- В других группах реагирует только на упоминание `@bot_username https://...`.
- В личке — на любую ссылку и на `/post <ссылка>`.

## Настройка Telegram
1. Бот у @BotFather → токен.
2. Бот — **администратор канала** с правом публикации.
3. Бот — участник чата; для групп отключить Privacy Mode (`/setprivacy` → Disable).
4. ID вида `-100XXXXXXXXXX` узнаются через `python whoami.py` (напиши что-нибудь
   в чат/канал → увидишь `chat.id=`).
