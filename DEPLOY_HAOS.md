# Деплой на Home Assistant OS (Raspberry Pi 4, arm64)

**Лучший способ — Docker Compose на хосте HAOS** (не аддон, не Portainer, просто `docker compose` в SSH).

---

## 1. Подготовка на HAOS
Включи **SSH-аддон** (Terminal & SSH) в HA:
- Настройки → Аддоны → Магазин → Terminal & SSH → Установить → Запустить
- В настройках аддона: **«Защита режима» выкл** (иначе не даст root-доступ)

---

## 2. Клонирование и конфиг
```bash
# В SSH HAOS (под root)
cd /root
git clone https://github.com/Lowbeat/gpc_bot.git
cd gpc_bot
```

**Конфиг через `.env`** (env-переменные имеют приоритет над `config.py`, см. `config_runtime.py`):
```bash
cat > .env << 'EOF'
BOT_TOKEN=1234567890:AAAA...
CHAT_ID=-1003743298448
CHANNEL_ID=-1004447835579
KEEP_ALIVE_MIN=10
EOF
```

> `.env` в `.gitignore` — секреты не уйдут в git. `config.py` не нужен.

---

## 3. Сборка и запуск
```bash
# Первый запуск — скачает образ и соберёт (2-5 мин на Pi 4)
docker compose up -d --build

# Логи
docker compose logs -f

# Остановка
docker compose down
```

---

## 4. Обновление бота
```bash
cd /root/gpc_bot
git pull
docker compose up -d --build  # пересоберёт только если есть изменения
```

---

## Важные нюансы

| Параметр | Значение |
|---|---|
| **Память** | Pi 4 4 Гб: HA ~1.3 Гб + бот с Chromium ~0.3 Гб = **запас есть** |
| **Образ** | `Dockerfile` использует `mcr.microsoft.com/playwright/python:v1.65.2-jammy-arm64` — нативный arm64, Chromium внутри для arm64 |
| **Перезапуск** | `restart: unless-stopped` в compose переживёт ребут Pi |
| **Сеть** | Исходящий трафик открыт, бот ходит в `api.telegram.org` и маркетплейсы |
| **Секреты** | Только в `.env`. `config_runtime.py` читает env → `config.py` не нужен |

---

## Почему не аддон / не Portainer
- `docker compose` на хосте — проще: `git pull && docker compose up -d --build`
- Не нужен лишний слой абстракции (аддоны, Portainer)
- Переменные окружения читаются напрямую через `config_runtime.py`
- Меньше точек отказа, понятнее дебажить

---

## Полезные команды
```bash
# Статус контейнера
docker compose ps

# Рестарт
docker compose restart

# Пересборка без кэша (если базовый образ обновился)
docker compose build --no-cache && docker compose up -d

# Удаление контейнера + образа (чистая пересборка)
docker compose down --rmi all && docker compose up -d --build
```
