"""Загрузка настроек: сначала переменные окружения, потом config.py.

Так удобно деплоить на хостинги (Render, Railway, Fly.io...): токен и ID
задаются через env, а в git они не попадают.

Две основные переменные:
  CHAT_ID    — чат (группа / обсуждение канала), где бот ловит ссылки;
  CHANNEL_ID — канал, куда бот публикует пост.

Если нужно несколько пар «чат -> канал», добавь CHAT_CHANNEL_MAP в формате
"-1003743298448:-1004447835579,-100XXXXXXXXXX:-100YYYYYYYYYY".
"""

import os

from config import BOT_TOKEN as _TOKEN_FROM_FILE
from config import CHAT_ID as _CHAT_FROM_FILE
from config import CHANNEL_ID as _CHANNEL_FROM_FILE

try:  # факультативно: несколько пар чат->канал в config.py
    from config import CHAT_CHANNEL_MAP as _MAP_FROM_FILE
except ImportError:
    _MAP_FROM_FILE = None

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


def _env_chat(name: str, default=None):
    """Читает chat id из env-переменной: '-100...' -> int, '@name' -> str."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return _as_chat(raw)


# Канал по умолчанию (куда постим, если для чата не задана своя привязка)
CHANNEL_ID = _env_chat("CHANNEL_ID", _CHANNEL_FROM_FILE)

# Чат по умолчанию (из него берём сообщения). Нужен, чтобы отличать его от
# остальных групп: в этом чате реагируем на любую ссылку, в прочих — только
# на тег бота.
CHAT_ID = _env_chat("CHAT_ID", _CHAT_FROM_FILE)


def _parse_pairs(raw):
    """Разбирает "чат:канал,чат:канал" в dict (int/'@name' как удобно)."""
    pairs = {}
    if isinstance(raw, dict):
        for chat, channel in raw.items():
            key = chat if isinstance(chat, int) else _as_chat(str(chat))
            val = channel if isinstance(channel, int) else _as_chat(str(channel))
            pairs[key] = val
        return pairs
    for item in str(raw or "").split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        chat, channel = item.split(":", 1)
        pairs[_as_chat(chat)] = _as_chat(channel)
    return pairs


# Привязка «чат -> канал» для нескольких каналов одновременно.
# Пример: "-1003743298448:-1004447835579"
CHAT_CHANNEL_MAP = _parse_pairs(os.environ.get("CHAT_CHANNEL_MAP"))

# Значение по умолчанию из config.py тоже может быть dict вида
# {chat: channel} или строкой "чат:канал".
if isinstance(_MAP_FROM_FILE, (dict, str)):
    for chat, channel in _parse_pairs(_MAP_FROM_FILE).items():
        CHAT_CHANNEL_MAP.setdefault(chat, channel)

if CHAT_ID and CHANNEL_ID:
    CHAT_CHANNEL_MAP.setdefault(CHAT_ID, CHANNEL_ID)


def resolve_channel(chat_id) -> int | str:
    """Канал-получатель для сообщения из чата `chat_id`."""
    return CHAT_CHANNEL_MAP.get(chat_id, CHANNEL_ID)


def channel_username(chat_id=None) -> str | None:
    """Юзернейм канала (для ссылки на пост), если канал публичный (@name)."""
    target = resolve_channel(chat_id)
    if isinstance(target, str) and target.startswith("@"):
        return target[1:]
    return None
