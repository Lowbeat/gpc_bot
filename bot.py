"""Telegram-бот: по тегу бота со ссылкой делает пост (картинка + название + ссылка) в канал."""

import asyncio
import io
import logging
import logging.handlers
import os
import sys
from pathlib import Path

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
from link_parser import extract_url, fetch_page, download_image, is_domain_allowed, _load_allowed_domains

# Setup logging: DEBUG to file, WARNING+ to console
log_file = Path(__file__).with_name("bot.log")
file_handler = logging.handlers.RotatingFileHandler(
    log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(levelname)s - %(message)s"
))

logging.basicConfig(
    level=logging.DEBUG,
    handlers=[file_handler, console_handler],
)
log = logging.getLogger("bot")

BOT_USERNAME = None


def escape(s: str) -> str:
    return s.replace("&", "&").replace("<", "<").replace(">", ">")


async def build_and_publish(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
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

    safe_url = url.replace("&", "&")
    user_name = msg.from_user.username or msg.from_user.first_name or "незнакомец"
    caption = f"<b>{escape(title)}</b>\n\n🔗 <a href=\"{safe_url}\">Читать полностью</a>\n\n👤 <i>От @{user_name}</i>"

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

    username = config.channel_username(chat.id if chat else None)
    ok_text = "✅ Опубликовано в канале!"
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

    text_parts = [msg.text or "", msg.caption or ""]
    text = " ".join(text_parts)
    log.debug("handle_message: text=%r", text)
    url = extract_url(text)
    log.debug("handle_message: extracted url=%r", url)

    # Fallback: try to get URL from Telegram entities
    if not url and msg.entities:
        for ent in msg.entities:
            if ent.type == "url" and msg.text and ent.offset is not None and ent.length is not None:
                url = msg.text[ent.offset:ent.offset + ent.length]
                log.debug("handle_message: entity fallback url=%r", url)
                break

    if not url:
        if msg.text and msg.text.strip().startswith(("http://", "https://")):
            url = msg.text.strip()
            log.debug("handle_message: fallback url=%r", url)

    if not url:
        allowed = _load_allowed_domains()
        if allowed:
            domains_str = ", ".join(sorted(allowed))
            await msg.reply_text(
                f"Я не нашёл ссылку 🙂\n"
                f"Работаю с доменами: {domains_str}\n"
                f"Пришли ссылку, например: https://www.ozon.ru/product/..."
            )
        else:
            await msg.reply_text("Я не нашёл ссылку 🙂 Пришли, например: https://example.com/article")
        return

    await build_and_publish(update, context, url)


def is_bot_mentioned(msg, bot_id: int = None) -> bool:
    if not BOT_USERNAME:
        return False
    uname = BOT_USERNAME.lower()

    for ent in list(msg.entities or []) + list(msg.caption_entities or []):
        # text_mention — это упоминание через @username в группе (с user object)
        if ent.type == "text_mention":
            if getattr(ent, "user", None):
                if bot_id is not None and ent.user.id == bot_id:
                    return True
                if ent.user.username and ent.user.username.lower() == uname:
                    return True

        # mention — это @username в тексте (без user object)
        if ent.type == "mention":
            raw = msg.text if msg.entities else msg.caption
            if raw and ent.offset is not None:
                token = raw[ent.offset: ent.offset + ent.length].lstrip("@").lower()
                if token == uname:
                    return True

        # fallback: entity с user object
        if getattr(ent, "user", None) and getattr(ent.user, "id", None):
            if bot_id is not None and ent.user.id == bot_id:
                return True
            if ent.user.username and ent.user.username.lower() == uname:
                return True

    text = ((msg.text or "") + " " + (msg.caption or "")).lower()
    if f"@{uname}" in text:
        return True

    replied = msg.reply_to_message
    if replied and replied.from_user:
        if bot_id is not None and replied.from_user.id == bot_id:
            return True
        if replied.from_user.username and replied.from_user.username.lower() == uname:
            return True

    return False


async def group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not is_bot_mentioned(msg, bot_id=context.bot.id):
        return
    await handle_message(update, context)


async def keep_awake() -> None:
    minutes = int(os.environ.get("KEEP_ALIVE_MIN", "10"))
    if minutes <= 0:
        return
    while True:
        await asyncio.sleep(minutes * 60)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(f"https://api.telegram.org/bot{config.BOT_TOKEN}/getMe")
            log.debug("keep-alive tick ok")
        except Exception as e:
            log.debug("keep-alive tick failed: %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling an update:", exc_info=context.error)


async def _post_init(application) -> None:
    global BOT_USERNAME
    me = await application.bot.get_me()
    BOT_USERNAME = me.username
    log.info("Бот запущен как @%s", BOT_USERNAME)

    # Start keep_awake task properly (avoid PTB warning)
    if int(os.environ.get("KEEP_ALIVE_MIN", "10")) > 0:
        application.create_task(keep_awake())


def main() -> None:
    # Fix for Python 3.15+ event loop issue
    if sys.version_info >= (3, 12):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )
    log.info("Бот стартует. чат=%s канал=%s", config.CHAT_ID, config.CHANNEL_ID)

    async def debug_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Временный: логирует ВСЕ сообщения в группах для отладки."""
        msg = update.message
        if msg:
            entities = [
                {"type": e.type, "offset": e.offset, "length": e.length, "user": getattr(e, "user", None)}
                for e in (msg.entities or [])
            ]
            log.info("DEBUG GROUP: chat_id=%s text=%r entities=%s", msg.chat_id, msg.text, entities)

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("post", handle_message))

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.TEXT | filters.CAPTION),
            handle_message,
        )
    )

    # Временный: логирует ВСЕ сообщения в группах
    app.add_handler(
        MessageHandler(
            (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP)
            & (filters.TEXT | filters.CAPTION),
            debug_group_handler,
        )
    )

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
