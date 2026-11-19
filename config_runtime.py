"""Загрузка настроек: сначала переменные окружения, потом config.py.

Так удобно деплоить на хостинги (PythonAnywhere, Render, Railway...):
токен и ID каналов задаются через env, а в git они не попадают.
"""

import os

from config import BOT_TOKEN as _TOKEN_FROM_FILE
from config import CHANNEL_ID as _CHANNEL_FROM_FILE

BOT_TOKEN = os.environ.get("BOT_TOKEN") or _TOKEN_FROM_FILE


def _env_chat(name: str, default=None):
    """Читает chat id из env: '-100...' -> int, '@name' -> str."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    raw = raw.strip()
    if raw.startswith("@"):
        return raw
    try:
        return int(raw)
    except ValueError:
        return raw


# Куда постим по умолчанию (если в сообщении не указан целевой канал)
CHANNEL_ID = _env_chat("CHANNEL_ID", _CHANNEL_FROM_FILE)

def _as_chat(raw: str):
    """'-100...' -> int, '@name' -> str как есть."""
    raw = raw.strip()
    if raw.startswith("@"):
        return raw
    try:
        return int(raw)
    except ValueError:
        return raw


# Привязка чат -> канал, например: "-1003743298448:-1004447835579"
CHAT_CHANNEL_MAP = {}
for item in os.environ.get("CHAT_CHANNEL_MAP", "").split(","):
    item = item.strip()
    if not item or ":" not in item:
        continue
    chat, channel = item.split(":", 1)
    CHAT_CHANNEL_MAP[_as_chat(chat)] = _as_chat(channel)


def resolve_channel(chat_id) -> int | str:
    """Канал-получатель для сообщения из чата `chat_id`.

    Если чат привязан через CHAT_CHANNEL_MAP — возвращаем привязанный канал,
    иначе — CHANNEL_ID по умолчанию.
    """
    return CHAT_CHANNEL_MAP.get(chat_id, CHANNEL_ID)
