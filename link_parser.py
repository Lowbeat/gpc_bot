"""Загрузка страницы по ссылке и извлечение метаданных (название, описание, картинка)."""

import html
import json
import re
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ru,en;q=0.8",
}

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# --- Allowed domains config ---
_ALLOWED_DOMAINS: set[str] | None = None


def _load_allowed_domains() -> set[str]:
    """Загружает список разрешённых доменов из JSON-конфига."""
    global _ALLOWED_DOMAINS
    if _ALLOWED_DOMAINS is not None:
        return _ALLOWED_DOMAINS

    config_path = Path(__file__).with_name("allowed_domains.json")
    if not config_path.exists():
        _ALLOWED_DOMAINS = set()
        return _ALLOWED_DOMAINS

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        domains = data.get("allowed_domains", [])
        # Нормализуем: убираем www., приводим к нижнему регистру
        _ALLOWED_DOMAINS = {d.lower().replace("www.", "") for d in domains}
    except Exception:
        _ALLOWED_DOMAINS = set()

    return _ALLOWED_DOMAINS


def is_domain_allowed(url: str) -> bool:
    """Проверяет, что домен ссылки входит в белый список.
    Если список пуст — разрешаем всё (обратная совместимость)."""
    allowed = _load_allowed_domains()
    if not allowed:
        return True
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return domain in allowed


def extract_url(text: str) -> str | None:
    """Достаёт первую ссылку из текста сообщения."""
    if not text:
        return None
    m = URL_RE.search(text)
    if not m:
        return None
    url = m.group(0).rstrip(").,!'\"»")
    # Telegram может прислать без протокола
    return url


def _is_valid_image_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _meta_content(soup: BeautifulSoup, **attrs) -> str | None:
    for tag in soup.find_all("meta"):
        ok = all((tag.get(k) or "").lower() == v.lower() for k, v in attrs.items())
        if ok and tag.get("content", "").strip():
            return tag["content"].strip()
    return None


def _abs(url: str, base: str) -> str:
    return urljoin(base, url)


async def fetch_page(url: str) -> dict:
    """Возвращает dict: title, description, image, link."""
    if not is_domain_allowed(url):
        raise ConnectionError(f"Домен не в списке разрешённых: {urlparse(url).netloc}")

    result = {"title": None, "description": None, "image": None, "link": url}
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=httpx.Timeout(15.0)
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            text = ""
            if "html" in ctype or "xml" in ctype or not ctype:
                text = resp.text
    except (httpx.HTTPError, socket.timeout) as e:
        raise ConnectionError(f"Не удалось загрузить страницу: {e}") from e

    if not text.strip():
        return result

    soup = BeautifulSoup(text, "html.parser")

    # --- Open Graph ---
    og_title = _meta_content(soup, property="og:title") or _meta_content(soup, name="og:title")
    og_desc = (
        _meta_content(soup, property="og:description")
        or _meta_content(soup, name="description")
        or _meta_content(soup, property="twitter:description")
    )
    og_image = (
        _meta_content(soup, property="og:image")
        or _meta_content(soup, name="og:image")
        or _meta_content(soup, name="twitter:image")
        or _meta_content(soup, property="twitter:image")
    )

    # --- <title> как фолбэк ---
    html_title = soup.title.get_text(strip=True) if soup.title else None

    result["title"] = html.unescape(og_title or html_title or "") or None
    result["description"] = html.unescape(og_desc or "") or None

    # --- картинка: og:image, иначе первый <img> с приличным src ---
    image = None
    if og_image and _is_valid_image_url(_abs(og_image, url)):
        image = _abs(og_image, url)
    else:
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original")
            if src and _is_valid_image_url(_abs(src, url)):
                candidate = _abs(src, url)
                # пропуск явных трекеров/мелких иконок по имени файла
                if not re.search(r"(sprite|pixel|blank|logo-?small|favicon)", candidate, re.I):
                    image = candidate
                    break

    # если это svg — telegram не примет как фото, но оставим, попробуем
    result["image"] = image

    # домен для подписи
    result["domain"] = urlparse(url).netloc.replace("www.", "")
    return result


async def download_image(client: httpx.AsyncClient, image_url: str, max_bytes: int = 5_000_000) -> bytes | None:
    """Скачивает картинку, возвращает bytes или None."""
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=httpx.Timeout(15.0)
        ) as c:
            r = await c.get(image_url)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if not ctype.startswith("image/") and not image_url.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                return None
            data = r.content[:max_bytes]
            return data if data else None
    except Exception:
        return None
