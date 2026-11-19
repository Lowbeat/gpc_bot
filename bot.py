"""
Telegram-бот: по тегу бота со ссылкой делает небольшой пост
(картинка + название + ссылка) и публикует его в канал.

Как использовать:
  1. pip install -r requirements.txt
  2. cp config.py.example config.py   -> заполнить BOT_TOKEN, CHAT_ID, CHANNEL_ID
     (на хостинге вместо этого задай эти же имена переменными окружения)
  3. python bot.py

Бот реагирует ТОЛЬКО на тег:
  - упоминание @username_bot со ссылкой в группе/чате — пост уходит в канал,
    привязанный к этому чату (CHAT_CHANNEL_MAP), иначе в CHANNEL_ID;
  - личное сообщение боту со ссылкой (в ЛС тег не нужен);
  - реплай на любое сообщение бота со ссылкой (считается тегом);
  - команду /post <ссылка>.
Любые сообщения без упоминания бота игнорируются полностью.
"""

import asyncio
import io
import logging
import os

import httpx
from telegram import Update, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

import config_runtime as config
from link_parser import extract_url, fetch_page, download_image

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
log = logging.getLogger("bot")

BOT_USERNAME = None  # подставится при старте


def escape(s: str) -> str:
    """Экранирование для HTML-парсинга Telegram."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def build_and_publish(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    """Готовит пост из ссылки и публикует его в канал."""
    chat = update.effective_chat
    msg = update.message or update.channel_post
    try:
        data = await fetch_page(url)
    except ConnectionError as e:
        await msg.reply_text(f"⚠️ {e}", reply_to_message_id=msg.message_id)
        return

    title = (data["title"] or data["domain"] or "Без названия").strip()
    if len(title) > 200:
        title = title[:197] + "..."

    # URL в HTML-тегах: & нужно экранировать, иначе Telegram не примет разметку
    safe_url = url.replace("&", "&amp;")
    caption = f"<b>{escape(title)}</b>\n\n🔗 <a href=\"{safe_url}\">Читать полностью</a>"

    image_bytes = None
    if data.get("image"):
        image_bytes = await download_image(context.client, data["image"])

    channel = config.resolve_channel(chat.id if chat else None)
    try:
        if image_bytes:
            sent = await context.bot.send_photo(
                chat_id=channel,
                photo=io.BytesIO(image_bytes),
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        else:
            sent = await context.bot.send_message(
                chat_id=channel,
                text=caption,
                parse_mode=ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
    except Exception as e:
        log.exception("Ошибка публикации в канал")
        await msg.reply_text(
            f"❌ Не удалось опубликовать в канал: {e}\n"
            "Проверьте, что бот добавлен в администраторы канала.",
            reply_to_message_id=msg.message_id,
        )
        return

    # Ссылка на пост в канале (работает только для публичного канала)
    username = config.channel_username(chat.id if chat else None)
    ok_text = "✅ Опубликовано в канал!"
    if username:
        ok_text += f"\nhttps://t.me/{username}/{sent.message_id}"
    await msg.reply_text(ok_text, reply_to_message_id=msg.message_id)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"Привет! Отправь мне ссылку (или тегни @{BOT_USERNAME} со ссылкой в чате) — "
        "я сделаю из неё короткий пост (картинка + название + ссылка) и опубликую в канал."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return

    # текст сообщения + возможная ссылка в entities / forwarded / web_preview
    text_parts = [msg.text or "", msg.caption or ""]
    if msg.entities:
        for ent in msg.entities:
            if ent.type == "text_link":
                pass  # URL недоступен напрямую
    text = " ".join(text_parts)
    url = extract_url(text)

    if not url:
        # если это сообщение-ссылка без текста
        if msg.text and msg.text.strip().startswith(("http://", "https://")):
            url = msg.text.strip()

    if not url:
        await msg.reply_text(
            "Я не нашёл ссылку 🙂 Пришли, например: https://example.com/article"
        )
        return

    await msg.reply_text("⏳ Готовлю пост...", reply_to_message_id=msg.message_id)
    await build_and_publish(update, context, url)


def is_bot_mentioned(msg, bot_id: int = None) -> bool:
    """Проверяет, тегнут ли бот в сообщении.

    Считается тегом:
      - прямое упоминание @username (в entities или текстом);
      - entity типа mention;
      - реплай на любое сообщение бота.
    """
    if not BOT_USERNAME:
        return False
    uname = BOT_USERNAME.lower()

    # 1) entities: text_link/mention с юзернеймом бота либо mentions
    for ent in list(msg.entities or []) + list(msg.caption_entities or []):
        if ent.type == "mention":
            raw = msg.text if msg.entities else msg.caption
            if raw and ent.offset is not None:
                token = raw[ent.offset: ent.offset + ent.length].lstrip("@").lower()
                if token == uname:
                    return True
        if getattr(ent, "user", None) and getattr(ent.user, "id", None):
            if bot_id is not None and ent.user.id == bot_id:
                return True
            if ent.user.username and ent.user.username.lower() == uname:
                return True

    # 2) просто текстом (без entities), например в caption у фото
    text = ((msg.text or "") + " " + (msg.caption or "")).lower()
    if f"@{uname}" in text:
        return True

    # 3) реплай сообщению бота тоже считаем тегом
    replied = msg.reply_to_message
    if replied and replied.from_user:
        if bot_id is not None and replied.from_user.id == bot_id:
            return True
        if replied.from_user.username and replied.from_user.username.lower() == uname:
            return True

    return False


async def group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка сообщений в группах/чатах — только по тегу бота."""
    msg = update.message
    if not is_bot_mentioned(msg, bot_id=context.bot.id):
        return  # без упоминания бот молчит даже в привязанном чате
    await handle_message(update, context)


async def keep_awake() -> None:
    """Держать free-план Render «бодрствующим».

    На бесплатном тарифе Render может усыпить сервис при отсутствии внешнего
    HTTP-трафика. Раз в KEEP_ALIVE_MIN минут делаем один лёгкий вызов Bot API,
    чтобы процесс не простаивал без дела. Выключается KEEP_ALIVE_MIN=0.
    """
    minutes = int(os.environ.get("KEEP_ALIVE_MIN", "10"))
    if minutes <= 0:
        return
    while True:
        await asyncio.sleep(minutes * 60)
        try:
            # getMe — самый дешёвый валидный вызов Bot API. Важно: это НЕ
            # «внешний HTTP-запрос» (ping), а всего лишь активность процесса,
            # поэтому на web-сервисах такой трюк не работает (см. DEPLOY.md).
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(f"https://api.telegram.org/bot{config.BOT_TOKEN}/getMe")
            log.debug("keep-alive tick ok")
        except Exception as e:  # noqa: BLE001 - просто фоновый пинг
            log.debug("keep-alive tick failed: %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling an update:", exc_info=context.error)


async def _post_init(application) -> None:
    global BOT_USERNAME
    me = await application.bot.get_me()
    BOT_USERNAME = me.username
    log.info("Бот запущен как @%s", BOT_USERNAME)
    if int(os.environ.get("KEEP_ALIVE_MIN", "10")) > 0:
        application.create_task(keep_awake())


def main() -> None:
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()
    # get_me() синхронно вызывать нельзя (нет event loop) — юзернейм в post_init
    app.add_post_init(_post_init)
    log.info(
        "Бот стартует. чат=%s канал=%s пар чат->канал=%d",
        config.CHAT_ID,
        config.CHANNEL_ID,
        len(config.CHAT_CHANNEL_MAP),
    )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("post", handle_message))

    # Личные сообщения — любая ссылка
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.TEXT | filters.CAPTION),
            handle_message,
        )
    )

    # Группы и супергруппы (включая привязанный чат канала) — только по тегу бота
    app.add_handler(
        MessageHandler(
            (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP)
            & (filters.TEXT | filters.CAPTION),
            group_handler,
        )
    )

    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
