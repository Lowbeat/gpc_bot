"""Telegram-бот: по тегу бота со ссылкой делает пост (картинка + название + ссылка) в канал."""

import asyncio
import io
import logging
import logging.handlers
import os
import socket
import sys
from pathlib import Path

import httpx
from telegram import Update, LinkPreviewOptions
from telegram.constants import ParseMode, MessageEntityType
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

from link_parser import HEADERS, download_image

import config_runtime as config
from link_parser import extract_url, fetch_page, is_domain_allowed, _load_allowed_domains

# Force IPv4 for Telegram API (Fly.io IPv6 routing issues)
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Force IPv4 resolution for all socket connections."""
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_getaddrinfo

# HTTP client with reasonable timeouts
http_client = httpx.AsyncClient(
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    timeout=httpx.Timeout(connect=60.0, read=60.0, write=60.0, pool=60.0),
    follow_redirects=True,
)

# Setup logging: DEBUG to file only if DEBUG=1, otherwise INFO; WARNING+ to console
debug_mode = os.environ.get("DEBUG", "0") == "1"
log_level = logging.DEBUG if debug_mode else logging.INFO

log_file = Path(__file__).with_name("bot.log")
file_handler = logging.handlers.RotatingFileHandler(
    log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
file_handler.setLevel(log_level)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(levelname)s - %(message)s"
))

logging.basicConfig(
    level=log_level,
    handlers=[file_handler, console_handler],
)
log = logging.getLogger("bot")

BOT_USERNAME = None


def escape(s: str) -> str:
    return s.replace("&", "&").replace("<", "<").replace(">", ">")


async def build_and_publish(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, comment: str = None) -> None:
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
    
    # Get author info - handle anonymous group admins
        # Get author info - handle anonymous group admins
    user = msg.from_user
    log.debug("build_and_publish: from_user=%s (id=%s, username=%s, first_name=%s, is_bot=%s)",
              user, user.id if user else None, user.username if user else None, user.first_name if user else None, user.is_bot if user else None)
    # Anonymous admin: username=GroupAnonymousBot, first_name=Group -> use chat title
    if user and user.username and user.username != "GroupAnonymousBot":
        user_name = f"@{user.username}"
    elif user and user.first_name and user.first_name != "Group":
        user_name = user.first_name
    elif msg.sender_chat:
        # Message sent on behalf of chat (channel/group) - use chat title
        user_name = msg.sender_chat.title or "чат"
    else:
        user_name = "незнакомец"

    # Build caption with price and store link
    domain = data.get("domain", "").lower()
    store_names = {
        "wildberries.ru": "Wildberries",
        "ozon.ru": "Ozon",
        "market.yandex.ru": "Яндекс Маркет",
        "aliexpress.ru": "AliExpress",
        "ali.click": "AliExpress",
    }
    store_name = store_names.get(domain, domain)

    # Price: from description (old) or price field (Playwright/API)
    price_line = ""
    if data.get("price"):
        price_val = data["price"]
        # Parse "sale (regular)" format
        if "(" in price_val and ")" in price_val:
            sale_part = price_val.split("(")[0].strip()
            regular_part = price_val.split("(")[1].split(")")[0].strip()
            price_line = f"💰 Цена: {sale_part}₽ ({regular_part}₽)\n"
        else:
            price_line = f"💰 Цена: {price_val}₽\n"
    elif data.get("description") and data["description"].startswith("Цена:"):
        price_line = f"💰 {data['description']}\n"

    store_link = f"<a href=\"{safe_url}\">{store_name}</a>"

    # Comment from user (text after URL)
    comment_line = ""
    if comment:
        comment_line = f"\n\n<blockquote>{escape(comment)}</blockquote>"

    caption = f"<b>{escape(title)}</b>\n\n{price_line}{store_link}{comment_line}\n\n👤 <i>От {user_name}</i>"

    image_bytes = None
    if data.get("image"):
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=httpx.Timeout(15.0)) as client:
            image_bytes = await download_image(client, data["image"])

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

    url = None
    comment = None

    # For /post command, extract URL after the command
    if msg.text and msg.text.startswith("/post"):
        parts = msg.text.split(maxsplit=1)
        if len(parts) > 1:
            rest = parts[1].strip()
            url_match = extract_url(rest)
            if url_match:
                url = url_match
                url_end = rest.find(url) + len(url)
                comment = rest[url_end:].strip()
            log.debug("handle_message: /post command url=%r comment=%r", url, comment)

    # Try to extract URL and comment from entities first (most accurate)
    if msg.entities:
        for ent in msg.entities:
            if ent.type == "url" and msg.text and ent.offset is not None and ent.length is not None:
                url = msg.text[ent.offset:ent.offset + ent.length]
                comment = msg.text[ent.offset + ent.length:].strip()
                log.debug("handle_message: entity url=%r comment=%r", url, comment)
                break

    # Fallback: caption entities
    if not url and msg.caption_entities:
        for ent in msg.caption_entities:
            if ent.type == "url" and msg.caption and ent.offset is not None and ent.length is not None:
                url = msg.caption[ent.offset:ent.offset + ent.length]
                comment = msg.caption[ent.offset + ent.length:].strip()
                log.debug("handle_message: caption entity url=%r comment=%r", url, comment)
                break

    # Fallback: extract_url from plain text (no comment extraction)
    if not url:
        url = extract_url(text)
        log.debug("handle_message: extracted url=%r", url)

    # Fallback: plain text starts with http
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

    await build_and_publish(update, context, url, comment)


def is_bot_mentioned(msg, bot_id: int = None) -> bool:
    if not BOT_USERNAME:
        log.debug("is_bot_mentioned: BOT_USERNAME not set")
        return False
    uname = BOT_USERNAME.lower()
    log.debug("is_bot_mentioned: uname=%r, bot_id=%s, text=%r", uname, bot_id, msg.text or msg.caption)

    for ent in list(msg.entities or []) + list(msg.caption_entities or []):
        log.debug("is_bot_mentioned: entity type=%s, offset=%s, length=%s, user=%s", ent.type, ent.offset, ent.length, getattr(ent, "user", None))
        # text_mention — это упоминание через @username в группе (с user object)
        if ent.type == MessageEntityType.TEXT_MENTION:
            if getattr(ent, "user", None):
                if bot_id is not None and ent.user.id == bot_id:
                    return True
                if ent.user.username and ent.user.username.lower() == uname:
                    return True

        # mention — это @username в тексте (без user object)
        if ent.type == MessageEntityType.MENTION:
            raw = msg.text if msg.entities else msg.caption
            if raw and ent.offset is not None:
                token = raw[ent.offset: ent.offset + ent.length].lstrip("@").lower()
                log.debug("is_bot_mentioned: mention token=%r, uname=%r, match=%s", token, uname, token == uname)
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
        log.debug("is_bot_mentioned: found @username in plain text")
        return True

    replied = msg.reply_to_message
    if replied and replied.from_user:
        if bot_id is not None and replied.from_user.id == bot_id:
            return True
        if replied.from_user.username and replied.from_user.username.lower() == uname:
            return True

    log.debug("is_bot_mentioned: no match found")
    return False


async def group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    # Ignore channel posts and bot's own messages
    if update.channel_post:
        return
    if msg.from_user and msg.from_user.id == context.bot.id:
        return
    log.debug("group_handler: chat_id=%s text=%r entities=%s",
              msg.chat_id, msg.text, msg.entities)
    if not is_bot_mentioned(msg, bot_id=context.bot.id):
        log.debug("group_handler: bot not mentioned, ignoring")
        return
    log.debug("group_handler: bot mentioned, processing")
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

    # Force IPv4 for Telegram API (Fly.io IPv6 routing issues)
        def _ipv4_resolver(host: str, port: int, family: int = socket.AF_INET, timeout: float = 5.0):
            """Resolve only IPv4 addresses."""
            infos = socket.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
            return [(socket.AF_INET, info[4]) for info in infos]

        # Custom HTTP transport with IPv4-only resolver
        class IPv4HTTPTransport(httpx.AsyncHTTPTransport):
            def __init__(self, *args, **kwargs):
                # Use custom resolver for IPv4 only
                from httpcore import AsyncConnectionPool, AsyncHTTPProxy
                pool = AsyncConnectionPool(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=60.0,
                    resolver=_ipv4_resolver,
                )
                super().__init__(*args, pool=pool, **kwargs)

        http_client = httpx.AsyncClient(
            transport=IPv4HTTPTransport(),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            timeout=httpx.Timeout(connect=60.0, read=60.0, write=60.0, pool=60.0),
            follow_redirects=True,
        )

        app = (
            ApplicationBuilder()
            .token(config.BOT_TOKEN)
            .http_client(http_client)
            .post_init(_post_init)
            .build()
        )
    log.info("Бот стартует. чат=%s канал=%s", config.CHAT_ID, config.CHANNEL_ID)

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("post", handle_message))

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.TEXT | filters.CAPTION),
            handle_message,
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
