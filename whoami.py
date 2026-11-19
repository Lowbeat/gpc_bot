"""Утилита для получения ID чатов и каналов.

Запуск:
    BOT_TOKEN=... python whoami.py
(или просто `python whoami.py`, если config.py заполнен)

Что делать:
  1. Напиши боту в нужном чате / канале любое сообщение (например «id»).
  2. В консоли появится chat.id — это то, что нужно для CHANNEL_ID / CHAT_CHANNEL_MAP.
Для приватного канала/чата бот должен быть добавлен туда участником (для каналов — админом).
"""

import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

import config_runtime as config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("whoami")


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    log.info(
        "chat.id=%s type=%s title=%r username=%s",
        chat.id,
        chat.type,
        chat.title,
        chat.username,
    )
    await update.message.reply_text(f"chat.id = {chat.id}\ntype = {chat.type}")


def main() -> None:
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, show_id))
    log.info("Слушаю сообщения. Пиши боту куда надо и смотри chat.id в логе. Ctrl+C — выход.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
