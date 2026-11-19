"""
Telegram-бот: по тегу (упоминанию) бота со ссылкой делает небольшой пост
(картинка + название + ссылка) и публикует его в канал.

Как использовать:
  1. pip install -r requirements.txt
  2. cp config.py.example config.py   -> заполнить BOT_TOKEN и CHANNEL_ID
  3. python bot.py

Бот реагирует на:
  - упоминание @username_bot с ссылкой в любом чате (в т.ч. приватном чате канала);
  - личное сообщение боту со ссылкой;
  - команду /post <ссылка>.
"""

import io
import logging

from telegram import Update, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

import config
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

    channel = config.CHANNEL_ID
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

    # Ссылка на пост в канале
    post_link = f"https://t.me/{_channel_username()}/{sent.message_id}" if _is_public_channel() else None
    ok_text = f"✅ Опубликовано в канал!"
    if post_link:
        ok_text += f"\n{post_link}"
    await msg.reply_text(ok_text, reply_to_message_id=msg.message_id)


def _is_public_channel() -> bool:
    return isinstance(config.CHANNEL_ID, str) and config.CHANNEL_ID.startswith("@")


def _channel_username() -> str | None:
    if _is_public_channel():
        return config.CHANNEL_ID[1:]
    return None


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


def is_bot_mentioned(msg) -> bool:
    """Проверяет, тегнут ли бот в сообщении (через entities или reply)."""
    if not BOT_USERNAME:
        return False
    text = (msg.text or "") + " " + (msg.caption or "")
    if f"@{BOT_USERNAME.lower()}" in text.lower():
        return True
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.username \
           and msg.reply_to_message.from_user.username.lower() == BOT_USERNAME.lower():
            return False  # реплай не считается тегом, но можно разрешить
    return False


async def group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """В группах/чатах отвечаем только если бот тегнут (или это ответ боту)."""
    msg = update.message
    mentioned = is_bot_mentioned(msg)
    replied_to_bot = bool(
        msg.reply_to_message
        and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == context.bot.id
    )
    if not (mentioned or replied_to_bot):
        return
    await handle_message(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling an update:", exc_info=context.error)


async def _post_init(application) -> None:
    global BOT_USERNAME
    me = await application.bot.get_me()
    BOT_USERNAME = me.username
    log.info("Бот запущен как @%s", BOT_USERNAME)


def main() -> None:
    global BOT_USERNAME
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()
    # get_me() синхронно вызывать нельзя (нет event loop) — получим юзернейм в post_init
    app.add_post_init(_post_init)
    log.info("Бот стартует, канал: %s", config.CHANNEL_ID)

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
