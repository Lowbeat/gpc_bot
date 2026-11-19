"""Показать chat.id чата/канала, в который бот имеет доступ.

Запуск:  python whoami.py [BOT_TOKEN]
Если токен не передан — берётся из переменной окружения BOT_TOKEN или config.py.

Дальше напиши что угодно в нужный чат или канал (или отправь боту в личку) —
в консоли появится строка вида:  chat.id=-1003743298448 type=supergroup title=...
"""

import asyncio
import os
import sys

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters


async def show(update: Update, context) -> None:
    chat = update.effective_chat
    src = update.message or update.channel_post or update.edited_message
    print(
        f"chat.id={chat.id} type={chat.type} title={chat.title!r} "
        f"user={src.from_user.id if src and src.from_user else None}",
        flush=True,
    )


async def run(token: str) -> None:
    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.ALL, show))
    me = await app.bot.get_me()
    print(f"Бот @{me.username} запущен, жду сообщения...", flush=True)
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await asyncio.sleep(60)
    await app.updater.stop()


def main() -> None:
    token = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BOT_TOKEN")
    if not token:
        from config import BOT_TOKEN as token

    asyncio.run(run(token))


if __name__ == "__main__":
    main()
