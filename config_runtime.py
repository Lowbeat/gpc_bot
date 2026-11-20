"""Загрузка настроек: переменные окружения имеют приоритет над config.py.

Две основные переменные:
  CHAT_ID    — чат (группа / обсуждение канала), где бот ловит ссылки;
  CHANNEL_ID — канал, куда бот публикует пост.

На хостинге (Render, Railway, Fly.io...) задаются через Environment Variables.
Локально — в config.py (он в .gitignore).
"""

import os

from config import BOT_TOKEN as _TOKEN_FROM_FILE
from config import CHAT_ID as _CHAT_FROM_FILE
from config import CHANNEL_ID as _CHANNEL_FROM_FILE

BOT_TOKEN = os.environ.get("BOT_TOKEN") or _TOKEN_FROM_FILE


def _as_chat(raw: str):
    """'-100...' -> int, '@name' -> str как есть."""
    raw = raw.strip()
    if raw.startswith("@"):
        return raw
    try:
        return int(raw)
    except ValueError:
        return raw


def _env_chat(name: str, default: int | str | None = None) -> int | str | None:
    """Читает chat id из env-переменной: '-100...' -> int, '@name' -> str."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return _as_chat(raw)


# Канал, куда публикуем пост
CHANNEL_ID: int | str = _env_chat("CHANNEL_ID", _CHANNEL_FROM_FILE) or _CHANNEL_FROM_FILE

# Чат, из которого берём ссылки (бот реагирует только на тег/реплай в этом чате)
CHAT_ID: int | str = _env_chat("CHAT_ID", _CHAT_FROM_FILE) or _CHAT_FROM_FILE


def resolve_channel(chat_id) -> int | str:
    """Канал-получатель для сообщения из чата `chat_id`.
    Всегда возвращает CHANNEL_ID — привязка 1:1."""
    return CHANNEL_ID


def channel_username(chat_id=None) -> str | None:
    """Юзернейм канала (для ссылки на пост), если канал публичный (@name)."""
    target = resolve_channel(chat_id)
    if isinstance(target, str) and target.startswith("@"):
        return target[1:]
    return None