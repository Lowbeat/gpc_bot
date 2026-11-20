# gpc_bot

Telegram-бот: ссылка → пост в канал (картинка + название + ссылка).

## Файлы
- `bot.py` — логика бота (python-telegram-bot, long polling)
- `link_parser.py` — загрузка страницы, извлечение `og:title` / `og:image`
- `config.example.py` — шаблон настроек (`BOT_TOKEN`, `CHAT_ID`, `CHANNEL_ID`)
- `config_runtime.py` — чтение настроек: env-переменные имеют приоритет над `config.py`
- `whoami.py` — утилита: показывает `chat.id` чата/канала, где было сообщение
- `DEPLOY.md` — бесплатный деплой (Render и др.) и про «засыпание» free-планов
- `requirements.txt` — зависимости проекта (`python-telegram-bot`, `httpx`, `beautifulsoup4`); ставить через `pip install -r requirements.txt`
- `allowed_domains.json` — белый список доменов для парсинга

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

## Как это работает
Бот постит **только когда его тегнут**:
- `@bot_username https://...` в группе/чате → пост уходит в канал (`CHANNEL_ID`);
- реплай сообщения бота со ссылкой тоже считается тегом;
- в личке тегать не нужно — любая ссылка (и `/post <ссылка>`);
- сообщения без упоминания бота игнорируются полностью.

Важно: чтобы бот видел упоминания в группах, у него должен быть выключен
Privacy Mode (`/setprivacy` → Disable) — иначе Telegram вообще не присылает
ему обычные сообщения, и тег до бота не дойдёт.

## Настройка Telegram
1. Бот у @BotFather → токен.
2. Бот — **администратор канала** с правом публикации.
3. Бот — участник чата; для групп отключить Privacy Mode (`/setprivacy` → Disable).
4. ID вида `-100XXXXXXXXXX` узнаются через `python whoami.py` (напиши что-нибудь
   в чат/канал → увидишь `chat.id=`).

## Backlog
- [x] **WB парсинг** — получаем название, картинку, цену (Playwright работает локально)
- [ ] **Ozon парсинг** — структура готова, локально блокирует VPN IP (188.243.70.16). На чистом IP (Fly.io/сервер) будет работать
- [x] **Yandex Market парсинг** — работает через HTTP (og:tags)
- [x] **AliExpress парсинг** — работает через HTTP (og:tags)
- [ ] **Playwright интеграция** — headless Chromium для WB/Ozon (антибот обходится только браузером)
- [ ] **Сборка сообщения** — пост в канал: картинка + заголовок + цена + ссылка + автор тега
- [ ] **Среднерыночная цена** — получение и отображение средней цены по маркетплейсам
- [ ] **Dockerfile** — для деплоя на Render/Fly.io (Playwright нужен контейнер с Chromium)
- [ ] **Unit tests** — для `link_parser` и `extract_url`
- [ ] **keep_awake** — опционально, `KEEP_ALIVE_MIN=0` выключает

Партнёрские API (WB/Ozon) — не нужны, доступов нет.
