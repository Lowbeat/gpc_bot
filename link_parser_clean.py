# -*- coding: utf-8 -*-
"""Загрузка страницы по ссылке и извлечение метаданных (название, описание, картинка)."""

import html
import json
import logging
import os
import re
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiofiles
import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("link_parser")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Ozon-specific headers (needs cookies for product pages)
OZON_HEADERS = {
    **HEADERS,
    "Referer": "https://www.ozon.ru/",
    "Origin": "https://www.ozon.ru/",
}

# WB API
WB_API_URL = "https://card.wb.ru/cards/v2/detail"
WB_API_URL_ALT = "https://catalog.wb.ru/cards/v2/detail"
WB_API_URL_MOBILE = "https://mobile.wb.ru/api/v2/detail"
WB_API_PARAMS = {
    "appType": "1",
    "curr": "rub",
    "dest": "-1257786",
    "spp": "30",
    "hide_dtype": "10",
    "ab_testing": "false",
    "lang": "ru",
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


async def _fetch_wb_api(nm_id: str) -> dict | None:
    """Получает данные товара WB через их API."""
    params = {**WB_API_PARAMS, "nm": nm_id}
    urls = [WB_API_URL, WB_API_URL_ALT, WB_API_URL_MOBILE]
    # Use additional headers that mimic browser requests
    api_headers = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
        "X-Requested-With": "XMLHttpRequest",
    }
    for url in urls:
        try:
            async with httpx.AsyncClient(headers=api_headers, timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(url, params=params)
                if resp.status_code in (403, 498):
                    log.debug("WB API %s returned %s", url, resp.status_code)
                    continue
                resp.raise_for_status()
                data = resp.json()
                products = data.get("data", {}).get("products", [])
                if products:
                    log.debug("WB API success from %s", url)
                    return products[0]
        except Exception as e:
            log.debug("WB API error for nm_id=%s url=%s: %s", nm_id, url, e)
    return None


async def _parse_yandex_market_jsonld(html_content: str, url: str) -> dict | None:
    """Парсит Яндекс Маркет через JSON-LD схему Product."""
    soup = BeautifulSoup(html_content, "html.parser")

    for script in soup.find_all("script", type="application/ld+json"):
        text = script.get_text()
        if '"@type":"Product"' not in text and '"@type": "Product"' not in text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue

        # Title
        title = html.unescape(data.get("name", "")).strip() or None

        # Price from offers
        price = None
        price_regular = None
        offers = data.get("offers")
        if isinstance(offers, list) and offers:
            offers = offers[0]
        if isinstance(offers, dict):
            # Main price
            p = offers.get("price")
            if p:
                price = str(int(float(p)))
            # Check for highPrice/lowPrice or priceSpecification
            price_spec = offers.get("priceSpecification")
            if isinstance(price_spec, dict):
                hp = price_spec.get("highPrice") or price_spec.get("maxPrice")
                if hp and hp != p:
                    price_regular = str(int(float(hp)))

        # Image
        image = None
        img = data.get("image")
        if isinstance(img, list) and img:
            img = img[0]
        if isinstance(img, str) and _is_valid_image_url(img):
            image = img
        elif isinstance(img, dict):
            img_url = img.get("contentUrl") or img.get("url")
            if img_url and _is_valid_image_url(img_url):
                image = img_url

        if not image:
            og_image = _meta_content(soup, property="og:image") or _meta_content(soup, name="og:image")
            if og_image and _is_valid_image_url(_abs(og_image, url)):
                image = _abs(og_image, url)

        return {
            "title": title,
            "price": f"{price} ({price_regular})" if price and price_regular else (price or price_regular),
            "image": image,
            "link": url,
            "domain": "market.yandex.ru",
        }

    return None


async def fetch_page(url: str) -> dict:
    """Возвращает dict: title, description, image, link."""
    if not is_domain_allowed(url):
        allowed = _load_allowed_domains()
        if allowed:
            domains_str = ", ".join(sorted(allowed))
            raise ConnectionError(
                f"Домен не в списке разрешённых: {urlparse(url).netloc}\n"
                f"Разрешены: {domains_str}"
            )
        raise ConnectionError(f"Домен не в списке разрешённых: {urlparse(url).netloc}")

    domain = urlparse(url).netloc.lower().replace("www.", "")
    headers = OZON_HEADERS if "ozon.ru" in domain else HEADERS

    # WB: try API first, then CDP (Edge on Windows, Chromium on Linux)
    if "wildberries.ru" in domain:
            m = re.search(r"/catalog/(\d+)", url)
            if m:
                wb_data = await _fetch_wb_api(m.group(1))
                if wb_data:
                    result = {"title": None, "description": None, "image": None, "link": url}
                    result["title"] = wb_data.get("name")
                    # WB API returns salePriceU (with card) and priceU (without card)
                    sale_price = wb_data.get("salePriceU")
                    price = wb_data.get("priceU")
                    if sale_price and price:
                        sale_price_rub = sale_price / 100
                        price_rub = price / 100
                        result["price"] = f"{sale_price_rub:.0f} ({price_rub:.0f})"
                        result["description"] = f"Цена: {sale_price_rub:.0f} ₽ ({price_rub:.0f} ₽)"
                    elif sale_price:
                        price_rub = sale_price / 100
                        result["price"] = f"{price_rub:.0f}"
                        result["description"] = f"Цена: {price_rub:.0f} ₽"
                    if wb_data.get("images"):
                        result["image"] = wb_data["images"][0]
                    elif wb_data.get("image"):
                        result["image"] = wb_data["image"]
                    result["domain"] = domain
                    return result

            # Fallback 1: Fast HTML parsing (no Playwright, much faster)
            log.debug("WB API failed, trying fast HTML parsing for %s", url)
            html_result = await _parse_wb_html_fast(url)
            if html_result and html_result.get("title"):
                return html_result

            # Fallback 2: CDP (Edge on Windows, Chromium on Linux ARM64)
            log.debug("WB HTML parsing failed, trying CDP for %s", url)
            cdp_result = await _parse_wb_cdp(url)
            if cdp_result and cdp_result.get("title"):
                if cdp_result.get("price"):
                    cdp_result["description"] = f"Цена: {cdp_result['price']} ₽"
                return cdp_result
            # CDP failed (likely challenge/antibot)
            raise ConnectionError("Wildberries: защита от ботов (challenge/antibot). Нужен residential IP или официальный API.")

    # Ozon: try CDP + internal API only (works on clean IP without proxy)
    if "ozon.ru" in domain:
        log.debug("Trying CDP + internal API for Ozon: %s", url)
        # Pass URL directly to CDP - it handles short URLs by navigating in browser
        cdp_result = await _parse_ozon_cdp(url)
        if cdp_result and cdp_result.get("title"):
            cdp_result["description"] = f"Цена: {cdp_result['price']} ₽" if cdp_result.get("price") else None
            return cdp_result
        # CDP failed - no Playwright fallback for Ozon (only Edge CDP works)
        raise ConnectionError("Ozon: VPN/защита от ботов. Нужен residential IP или официальный API.")

    # AliExpress: basic HTTP (og:tags) — skip Playwright due to captcha
    if "aliexpress.ru" in domain or "ali.click" in domain:
        # Fall through to default HTTP parsing for og:title/og:image
        pass

    # Default HTTP parsing for other domains (Yandex Market, AliExpress, etc.)
    try:
        # Use minimal headers for Yandex Market to get full HTML with JSON-LD
        if "market.yandex.ru" in domain:
            request_headers = {"User-Agent": HEADERS["User-Agent"]}
        else:
            request_headers = headers
        async with httpx.AsyncClient(
            headers=request_headers, follow_redirects=True, timeout=httpx.Timeout(15.0)
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
        return {"title": None, "description": None, "image": None, "link": url, "domain": domain}

    # Yandex Market: try JSON-LD first (after we have the HTML)
    if "market.yandex.ru" in domain:
        log.debug("Trying JSON-LD for Yandex Market: %s", url)
        ym_result = await _parse_yandex_market_jsonld(text, url)
        if ym_result and ym_result.get("title"):
            return ym_result

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

    result = {
        "title": html.unescape(og_title or html_title or "") or None,
        "description": html.unescape(og_desc or "") or None,
        "image": None,
        "link": url,
        "domain": domain,
    }

    # --- картинка: og:image, иначе первый <img> с приличным src ---
    image = None
    if og_image and _is_valid_image_url(_abs(og_image, url)):
        image = _abs(og_image, url)
    else:
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original")
            if src and _is_valid_image_url(_abs(src, url)):
                candidate = _abs(src, url)
                if not re.search(r"(sprite|pixel|blank|logo-?small|favicon)", candidate, re.I):
                    image = candidate
                    break

    result["image"] = image
    return result


def extract_url(text: str) -> str | None:
    """Достаёт первую ссылку из текста сообщения."""
    if not text:
        return None
    m = URL_RE.search(text)
    if not m:
        # Fallback: try to find any http/https URL more broadly
        m = re.search(r"https?://[^\s\"'>)]+", text, re.IGNORECASE)
    if not m:
        return None
    url = m.group(0).rstrip(").,!'\"»")
    return url


async def download_image(image_url: str, max_bytes: int = 5_000_000) -> bytes | None:
    """Скачивает картинку или читает локальный файл, возвращает bytes или None."""
    try:
        # Check if it's a local file path
        if image_url.startswith(("D:/", "C:/", "/")) and not image_url.startswith("http"):
            path = Path(image_url)
            if path.exists() and path.is_file():
                async with aiofiles.open(path, "rb") as f:
                    data = await f.read(max_bytes)
                    return data if data else None
        
        # Otherwise download via HTTP
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


# --- Playwright parsing (for anti-bot sites: WB, Ozon) ---

async def _fetch_with_playwright(url: str, wait_selector: str | None = None, wait_timeout: int = 90000) -> str | None:
    """Загружает страницу через Playwright (Chromium), возвращает HTML."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("Playwright not installed, skipping browser fetch")
        return None

    try:
        async with async_playwright() as p:
            # headless: configurable via env PLAYWRIGHT_HEADLESS (default true for servers)
            headless = os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
            browser = await p.chromium.launch(
                headless=headless,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                ]
            )
            context = await browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="ru-RU",
                viewport={"width": 1280, "height": 720},
                java_script_enabled=True,
                bypass_csp=True,
            )
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=wait_timeout)

            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=wait_timeout)
                except Exception:
                    pass
            else:
                # Try to wait for network to settle, but don't block forever
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

            # Small buffer for final rendering
            await page.wait_for_timeout(2000)

            html_content = await page.content()
            print(f"DEBUG: Got HTML length: {len(html_content)}")
            await browser.close()
            return html_content
    except Exception as e:
        log.debug("Playwright fetch failed for %s: %s", url, e)
        import traceback
        traceback.print_exc()
        return None

async def _parse_wb_playwright(url: str) -> dict | None:
    """Парсит WB через Playwright (обходит 403/498)."""
    # Wait for price elements to appear (wallet price or regular price)
    html_content = await _fetch_with_playwright(
        url,
        wait_selector="span[class*='productLinePriceWallet'], span[class*='productLinePriceNow'], [data-auto='price']",
        wait_timeout=15000
    )
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, "html.parser")

    # Title: WB uses dynamic class names like productImtName--QcvL_
    title = None
    for sel in ["span[class*='productImtName']", "h1[class*='title']", "h1"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title = html.unescape(el.get_text(strip=True))
            break

    # Price: try to get both wallet price (with card) and regular price (without card)
    price = None
    sale_price = None
    regular_price = None
    
    # Wallet price (with card) - usually the main price shown
    for sel in ["span[class*='productLinePriceWallet']", "[class*='price'] [class*='current']", "[data-auto='price']"]:
        el = soup.select_one(sel)
        if el:
            price_text = el.get_text(strip=True)
            import re
            m = re.search(r"([\d\s]+)", price_text.replace("\u00a0", "").replace(" ", ""))
            if m:
                sale_price = m.group(1).replace(" ", "")
                break
    
    # Regular price (without card) - NOT the crossed-out old price, but the second price
    # Try specific WB selectors for "price without card"
    for sel in [
        "span[class*='productLinePriceNow']",  # NOW price (without card)
        "span[class*='productLinePriceDefault']",  # default/regular price
        "span[class*='price-block__price']:not([class*='Wallet'])",  # non-wallet price
        "[class*='price-block'] span:not([class*='Wallet']):not([class*='Old'])",  # generic
    ]:
        el = soup.select_one(sel)
        if el:
            price_text = el.get_text(strip=True)
            import re
            m = re.search(r"([\d\s]+)", price_text.replace("\u00a0", "").replace(" ", ""))
            if m:
                regular_price = m.group(1).replace(" ", "")
                # Sanity check: regular price should be >= sale price
                if sale_price and int(regular_price) >= int(sale_price):
                    break
                regular_price = None
    
    # Fallback: old price (crossed out) - but validate it's reasonable
    if not regular_price:
        for sel in ["span[class*='productLinePriceOld']", "[class*='price'] [class*='old']", "span[class*='old-price']", "[class*='price-old']"]:
            el = soup.select_one(sel)
            if el:
                price_text = el.get_text(strip=True)
                import re
                m = re.search(r"([\d\s]+)", price_text.replace("\u00a0", "").replace(" ", ""))
                if m:
                    candidate = m.group(1).replace(" ", "")
                    # Only use if it's >= sale price and not absurdly high (like 18500 for a 668 item)
                    if sale_price and int(candidate) >= int(sale_price) and int(candidate) <= int(sale_price) * 3:
                        regular_price = candidate
                    break
    
    # Format price: "sale (regular)" if both found, otherwise just sale
    if sale_price and regular_price:
        price = f"{sale_price} ({regular_price})"
    elif sale_price:
        price = sale_price
    elif regular_price:
        price = regular_price

    # Image: WB serves from basket-XX.wbbasket.ru
    image = None
    og_image = _meta_content(soup, property="og:image") or _meta_content(soup, name="og:image")
    if og_image and _is_valid_image_url(_abs(og_image, url)):
        image = _abs(og_image, url)
    else:
        for sel in ["img[src*='basket-'][src*='wbbasket'][src*='images']", "img[src*='images'][src*='webp']"]:
            img = soup.select_one(sel)
            if img:
                src = img.get("src") or img.get("data-src")
                if src and _is_valid_image_url(_abs(src, url)):
                    image = _abs(src, url)
                    break

    # Fix WB thumbnail URL to get full-size image
        # WB uses /c246x328/ in path for thumbnails, replace with /orig/
        # Don't replace /big/ as it may be the best available for some products
        if image:
            import re
            image = re.sub(r'/c\d+x\d+/', '/orig/', image)

    return {"title": title, "price": price, "image": image, "link": url, "domain": "wildberries.ru"}

async def _parse_ozon_playwright(url: str) -> dict | None:
    """Парсит Ozon через Playwright (обходит 403/anti-bot)."""
    html_content = await _fetch_with_playwright(
        url,
        wait_selector="h1[data-auto='product-title'], h1[class*='heading'], h1",
        wait_timeout=15000
    )
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, "html.parser")

    # Check for VPN/challenge page
    if ("выключите vpn" in html_content.lower() or "подозритель" in html_content.lower() 
        or "captcha" in html_content.lower() or "похоже, нет соединения" in html_content.lower()):
        log.debug("Ozon challenge/VPN block detected")
        return None

    # Title
    title = None
    for sel in ["h1[data-auto='product-title']", "h1[class*='heading']", "h1"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title = html.unescape(el.get_text(strip=True))
            break

    # Price
    price = None
    for sel in ["[data-auto='price-value']", "[class*='price'] [class*='current']", "[class*='price'] span:first-child"]:
        el = soup.select_one(sel)
        if el:
            price_text = el.get_text(strip=True)
            import re
            m = re.search(r"([\d\s]+)", price_text.replace("\u00a0", "").replace(" ", ""))
            if m:
                price = m.group(1).replace(" ", "")
                break

    # Image
    image = None
    og_image = _meta_content(soup, property="og:image") or _meta_content(soup, name="og:image")
    if og_image and _is_valid_image_url(_abs(og_image, url)):
        image = _abs(og_image, url)
    else:
        for sel in ["[data-auto='product-gallery'] img", "[class*='gallery'] img", "img[src*='ozon']"]:
            img = soup.select_one(sel)
            if img:
                src = img.get("src") or img.get("data-src")
                if src and _is_valid_image_url(_abs(src, url)):
                    image = _abs(src, url)
                    break

    return {"title": title, "price": price, "image": image, "link": url, "domain": "ozon.ru"}


async def _parse_ozon_cdp(url: str) -> dict | None:
    """Парсит Ozon через Playwright CDP + внутренний API (обходит антибот)."""
    # First check if playwright is available
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("Playwright not installed, skipping CDP fetch")
        return None

    import asyncio
    import platform
    import re
    
    # Detect platform
    is_arm64_linux = platform.system() == "Linux" and platform.machine() in ("aarch64", "arm64")
    
    # Edge process for Windows (needs cleanup in finally)
    edge_proc = None
    
    try:
        if is_arm64_linux:
            # Linux ARM64: use Playwright's built-in Chromium with CDP
            result = await _parse_ozon_linux_cdp(url)
            return result
        else:
            # Windows: use Edge as before
            result = await _parse_ozon_windows_cdp(url)
            return result
    except Exception as e:
        log.debug("Ozon CDP fetch failed for %s: %s", url, e)
        import traceback
        traceback.print_exc()
        return None
    finally:
        # Cleanup Edge process on Windows
        if edge_proc is not None:
            try:
                edge_proc.terminate()
            except Exception:
                pass


async def _parse_ozon_linux_cdp(url: str) -> dict | None:
    """Парсит Ozon на Linux ARM64 через Playwright Chromium CDP."""
    from playwright.async_api import async_playwright
    import json
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )
        try:
            context = await browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="ru-RU",
                viewport={"width": 1280, "height": 720},
                java_script_enabled=True,
                bypass_csp=True,
            )
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            page = await context.new_page()
            
            # Go to main page to establish session
            await page.goto("https://www.ozon.ru", wait_until="domcontentloaded", timeout=30000)
            # Wait for challenge to pass
            for i in range(40):
                title = await page.title()
                if "Captcha" in title:
                    print(f"DEBUG: Captcha detected at attempt {i+1}")
                    break
                if "нет соединения" in title:
                    print(f"DEBUG: Challenge page at attempt {i+1}")
                    break
                ready = await page.evaluate("document.querySelectorAll('[id^=\"state-\"]').length")
                if ready and "Antibot" not in title:
                    print(f"DEBUG: Challenge passed at attempt {i+1}")
                    break
                await page.wait_for_timeout(1000)
            else:
                print("DEBUG: Challenge timeout")
            
            await page.wait_for_timeout(3000)
            
            # Extract product ID from URL (handle short URLs by navigating in browser)
            product_id = None
            final_url = url
            
            # First try to extract from full product URL - match product ID at end of path
            # Pattern: /product/...-<id>/ or /product/...-<id>? or /product/<id>/
            m = re.search(r"/product/.*[-_](\d+)(?:/|\?)", final_url)
            if m:
                product_id = m.group(1)
            else:
                # Fallback: try to find any numeric ID at end of path
                m = re.search(r"/(\d+)(?:/|\?)", final_url)
                if m:
                    product_id = m.group(1)
            
            # If no product_id found and it's a short URL (ozon.ru/t/...), navigate in browser
            if not product_id and "/t/" in url:
                log.debug("Navigating to short URL in browser to resolve: %s", url)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Wait for redirect to complete and challenge to pass on product page
                for i in range(20):
                    await page.wait_for_timeout(1000)
                    title = await page.title()
                    if "Captcha" in title or "нет соединения" in title:
                        log.debug("Challenge on product page at attempt %d", i+1)
                        continue
                    break
                await page.wait_for_timeout(3000)
                final_url = page.url
                log.debug("Resolved short URL in browser: %s -> %s", url, final_url)
                # Extract product ID from resolved URL
                m = re.search(r"/product/.*[-_](\d+)(?:/|\?)", final_url)
                if m:
                    product_id = m.group(1)
                else:
                    m = re.search(r"/(\d+)(?:/|\?)", final_url)
                    if m:
                        product_id = m.group(1)
            
            if not product_id:
                log.debug("Could not extract product ID from URL: %s", url)
                await browser.close()
                return None
            
            # Call internal API using the product ID
            api_url = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=%2Fproduct%2F{product_id}%2F"
            log.debug("Calling Ozon API: %s", api_url)
            fetch_js = (
                "async ({url}) => {"
                "const res = await fetch(url, {"
                "method: 'GET',"
                "credentials: 'include',"
                "headers: {'Accept': 'application/json', 'Content-Type': 'application/json'}"
                "});"
                "let json = null;"
                "try { json = await res.json(); } catch (e) {}"
                "return {status: res.status, url: res.url, json};"
                "}"
            )
            result = await page.evaluate(fetch_js, {"url": f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=%2Fproduct%2F{product_id}%2F"})
            
            print(f"DEBUG: API status={result['status']}, has_json={bool(result['json'])}")
            
            await browser.close()
            
            if result["status"] != 200 or not result["json"]:
                log.debug("Ozon CDP API failed: status=%s", result["status"])
                return None
            
            # Parse widgetStates
            widget_states = result["json"].get("widgetStates", {})
            print(f"DEBUG: widget_states count={len(widget_states)}")
            
            # Title from webProductHeading
            title = None
            for key, value in widget_states.items():
                if key.startswith("webProductHeading"):
                    try:
                        data = json.loads(value)
                        title = data.get("title")
                        if title:
                            break
                    except Exception:
                        pass

            # Price from webPrice (and variants like webPriceDecreasedCompact)
            card_price = None
            regular_price = None
            original_price = None
            # Collect all webPrice widgets, prefer the one with actual price data
            webprice_widgets = []
            for key, value in widget_states.items():
                if key.startswith("webPrice"):
                    try:
                        data = json.loads(value)
                        webprice_widgets.append((key, data))
                    except Exception:
                        pass

            # First, try to find a widget with actual numeric prices
            numeric_price_widget = None
            for key, data in webprice_widgets:
                card_price = data.get("cardPrice")
                regular_price = data.get("price")
                original_price = data.get("originalPrice")
                # Check if this widget has actual numeric prices
                has_numeric_price = any(v for v in [card_price, regular_price, original_price] if v and any(c.isdigit() for c in str(v)))
                if has_numeric_price:
                    numeric_price_widget = (key, data, card_price, regular_price, original_price)
                    break

            # If no widget with numeric prices, fall back to first widget (might have textRs only)
            if numeric_price_widget is None and webprice_widgets:
                key, data = webprice_widgets[0]
                card_price = data.get("cardPrice")
                regular_price = data.get("price")
                original_price = data.get("originalPrice")
                text_rs = data.get("textRs", [])
                if card_price is None and text_rs and isinstance(text_rs, list):
                    card_price = text_rs[0].get("content")
            else:
                key, data, card_price, regular_price, original_price = numeric_price_widget

            # Format price: "cardPrice (regularPrice)" like WB
            price_parts = []
            if card_price:
                card_clean = re.sub(r"[^\d]", "", card_price)
                price_parts.append(card_clean)
            if regular_price and regular_price != card_price:
                reg_clean = re.sub(r"[^\d]", "", regular_price)
                price_parts.append(reg_clean)
            # Skip original_price unless it's different from regular
            if original_price and original_price != regular_price:
                orig_clean = re.sub(r"[^\d]", "", original_price)
                price_parts.append(orig_clean)
            if len(price_parts) >= 2:
                price = f"{price_parts[0]} ({price_parts[1]})"
            elif len(price_parts) == 1:
                price = price_parts[0]
            else:
                price = None

            # Image from webGallery (higher quality) or webStickyProducts/webPdpGrid
            image = None
            for key, value in widget_states.items():
                if key.startswith("webGallery"):
                    try:
                        data = json.loads(value)
                        # Get first image from gallery (highest quality)
                        images = data.get("images", [])
                        if images:
                            img_url = images[0].get("src") or images[0].get("imageUrl") or images[0].get("url")
                            if img_url and img_url.startswith("http"):
                                # Remove size constraints like /c50/ for higher quality (like WB /orig/)
                                image = re.sub(r"/c\d+/", "/", img_url)
                                break
                    except Exception:
                        pass

            # Fallback to webStickyProducts or webPdpGrid
            if not image:
                for key, value in widget_states.items():
                    if key.startswith("webStickyProducts") or key.startswith("webPdpGrid"):
                        try:
                            data = json.loads(value)
                            img_url = data.get("coverImageUrl") or data.get("imageUrl") or data.get("image")
                            if img_url and img_url.startswith("http"):
                                # Remove size constraints
                                image = re.sub(r"/c\d+/", "/", img_url)
                                break
                        except Exception:
                            pass

            # Fallback to og:image
            if not image:
                pass

            return {"title": title, "price": price, "image": image, "link": url, "domain": "ozon.ru"}
        except Exception as e:
            log.debug("Ozon CDP fetch failed for %s: %s", url, e)
            import traceback
            traceback.print_exc()
            return None



async def _parse_wb_cdp(url: str) -> dict | None:
    """Парсит WB через CDP + браузер (обходит антибот)."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("Playwright not installed, skipping CDP fetch")
        return None

    import asyncio
    import platform
    import re

    # Detect platform
    is_arm64_linux = platform.system() == "Linux" and platform.machine() in ("aarch64", "arm64")

    # Edge process for Windows (needs cleanup in finally)
    edge_proc = None

    try:
        if is_arm64_linux:
            # Linux ARM64: use Playwright's built-in Chromium with CDP
            result = await _parse_wb_linux_cdp(url)
            return result
        else:
            # Windows: use Edge as before
            result = await _parse_wb_windows_cdp(url)
            return result
    except Exception as e:
        log.debug("WB CDP fetch failed for %s: %s", url, e)
        import traceback
        traceback.print_exc()
        return None
    finally:
        # Cleanup Edge process on Windows
        if edge_proc is not None:
            try:
                edge_proc.terminate()
            except Exception:
                pass


async def _parse_wb_linux_cdp(url: str) -> dict | None:
    """Парсит WB на Linux ARM64 через Playwright Chromium CDP."""
    from playwright.async_api import async_playwright
    from bs4 import BeautifulSoup
    import html

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )
        try:
            context = await browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="ru-RU",
                viewport={"width": 1280, "height": 720},
                java_script_enabled=True,
                bypass_csp=True,
            )
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            page = await context.new_page()

            # Go directly to product page - challenge happens there anyway
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            
            # First, wait for challenge to pass (up to 30s)
            # Check for actual challenge PAGE (title/overlay), not just scripts in DOM
            challenge_passed = False
            for i in range(30):
                try:
                    title = await page.title()
                    
                    # Check for challenge interstitial page indicators
                    challenge_page = False
                    if "Доступ ограничен" in title or "Доступ запрещен" in title or "Challenge" in title:
                        challenge_page = True
                    else:
                        # Check for visible challenge overlay/iframe
                        try:
                            overlay = await page.query_selector('[class*="challenge-overlay"], [class*="antibot-overlay"], iframe[src*="antibot"], iframe[src*="challenge"]')
                            if overlay:
                                is_visible = await overlay.is_visible()
                                if is_visible:
                                    challenge_page = True
                        except Exception:
                            pass
                    
                    if challenge_page:
                        print(f"DEBUG WB: Challenge page at attempt {i+1}")
                    else:
                        print(f"DEBUG WB: Challenge passed at attempt {i+1}")
                        challenge_passed = True
                        break
                        
                    await page.wait_for_timeout(1000)
                except Exception:
                    await page.wait_for_timeout(1000)
            else:
                print("DEBUG WB: Challenge timeout, continuing anyway")
                challenge_passed = True  # Continue anyway

            # Then wait for product elements (up to 5s - data usually ready after challenge)
            for i in range(5):
                try:
                    ready = await page.evaluate('document.querySelectorAll("[class*=\"productLinePriceWallet\"], [class*=\"productLinePriceNow\"], [data-auto=\"price\"], [data-testid=\"product-price-wallet\"], [data-testid=\"product-price-final\"]").length')
                    if ready:
                        print(f"DEBUG WB: Product elements ready at attempt {i+1}")
                        break
                except Exception:
                    pass
                await page.wait_for_timeout(1000)
            else:
                print("DEBUG WB: Product elements timeout, continuing anyway")

            await page.wait_for_timeout(500)

            html_content = await page.content()
            print(f"DEBUG WB: Got HTML length: {len(html_content)}")
            await browser.close()

            return _parse_wb_html(html_content, url)

        except Exception as e:
            log.debug("WB CDP fetch failed for %s: %s", url, e)
            import traceback
            traceback.print_exc()
            return None


async def _parse_wb_windows_cdp(url: str) -> dict | None:
    import asyncio
    """Парсит WB на Windows через Edge CDP."""
    import os
    import tempfile, socket, subprocess
    from playwright.async_api import async_playwright
    from bs4 import BeautifulSoup
    import html

    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge_path = None
    for p in edge_paths:
        if os.path.exists(p):
            edge_path = p
            break
    if not edge_path:
        log.debug("Edge not found, skipping CDP fetch")
        return None

    def free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    cdp_port = free_port()
    edge_proc = subprocess.Popen([
        edge_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={tempfile.mkdtemp(prefix='wb-edge-')}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ])

    try:
        await asyncio.sleep(3)
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            context = browser.contexts[0]
            page = await context.new_page()

            # Go directly to product page - challenge happens there anyway
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            
            # First, wait for challenge to pass (up to 30s)
            # Check for actual challenge PAGE (title/overlay), not just scripts in DOM
            challenge_passed = False
            for i in range(30):
                try:
                    title = await page.title()
                    
                    # Check for challenge interstitial page indicators
                    challenge_page = False
                    if "Доступ ограничен" in title or "Доступ запрещен" in title or "Challenge" in title:
                        challenge_page = True
                    else:
                        # Check for visible challenge overlay/iframe
                        try:
                            overlay = await page.query_selector('[class*="challenge-overlay"], [class*="antibot-overlay"], iframe[src*="antibot"], iframe[src*="challenge"]')
                            if overlay:
                                is_visible = await overlay.is_visible()
                                if is_visible:
                                    challenge_page = True
                        except Exception:
                            pass
                    
                    if challenge_page:
                        print(f"DEBUG WB: Challenge page at attempt {i+1}")
                    else:
                        print(f"DEBUG WB: Challenge passed at attempt {i+1}")
                        challenge_passed = True
                        break
                        
                    await page.wait_for_timeout(1000)
                except Exception:
                    await page.wait_for_timeout(1000)
            else:
                print("DEBUG WB: Challenge timeout, continuing anyway")
                challenge_passed = True  # Continue anyway

            # Then wait for product elements (up to 5s - data usually ready after challenge)
            for i in range(5):
                try:
                    ready = await page.evaluate('document.querySelectorAll("[class*=\"productLinePriceWallet\"], [class*=\"productLinePriceNow\"], [data-auto=\"price\"], [data-testid=\"product-price-wallet\"], [data-testid=\"product-price-final\"]").length')
                    if ready:
                        print(f"DEBUG WB: Product elements ready at attempt {i+1}")
                        break
                except Exception:
                    pass
                await page.wait_for_timeout(1000)
            else:
                print("DEBUG WB: Product elements timeout, continuing anyway")

            await page.wait_for_timeout(500)

            html_content = await page.content()
            print(f"DEBUG WB: Got HTML length: {len(html_content)}")
            await browser.close()

            return _parse_wb_html(html_content, url)

    except Exception as e:
        log.debug("WB CDP fetch failed for %s: %s", url, e)
        import traceback
        traceback.print_exc()
        return None
    finally:
        try:
            edge_proc.terminate()
        except Exception:
            pass


def _parse_wb_html(html_content: str, url: str) -> dict | None:
    """Parse WB HTML content (shared logic for both platforms)."""
    from bs4 import BeautifulSoup
    import html
    import re

    soup = BeautifulSoup(html_content, "html.parser")

    # Title: WB uses dynamic class names like productTitle--ktP8Z
    title = None
    for sel in ["[class*='productTitle']", "h1[class*='title']", "h1", "h2[class*='productTitle']"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title = html.unescape(el.get_text(strip=True))
            break

    # Price: try to get both wallet price (with card) and regular price (without card)
    price = None
    sale_price = None
    regular_price = None

    # Wallet price (with card) - new WB uses data-testid
    for sel in [
        "[data-testid='product-price-wallet']",  # new WB wallet price button
        "[data-testid='product-price']",  # generic product price
        "span[class*='productLinePriceWallet']",  # old selector fallback
        "[class*='price'] [class*='current']",
        "[data-auto='price']",
    ]:
        el = soup.select_one(sel)
        if el:
            price_text = el.get_text(strip=True)
            m = re.search(r"([\d\s]+)", price_text.replace(" ", "").replace(" ", ""))
            if m:
                sale_price = m.group(1).replace(" ", "")
                break

    # Regular price (without card) - NOT the crossed-out old price, but the second price
    # New WB uses data-testid="product-price-final" for the final price
    for sel in [
        "[data-testid='product-price-final']",  # new WB final price (without card)
        "span[class*='productLinePriceNow']",  # old NOW price (without card)
        "span[class*='productLinePriceDefault']",  # old default/regular price
        "span[class*='price-block__price']:not([class*='Wallet'])",  # old non-wallet price
        "[class*='price-block'] span:not([class*='Wallet']):not([class*='Old'])",  # old generic
    ]:
        el = soup.select_one(sel)
        if el:
            price_text = el.get_text(strip=True)
            m = re.search(r"([\d\s]+)", price_text.replace(" ", "").replace(" ", ""))
            if m:
                regular_price = m.group(1).replace(" ", "")
                # Sanity check: regular price should be >= sale price
                if sale_price and int(regular_price) >= int(sale_price):
                    break
                regular_price = None

    # Fallback: old price (crossed out) - but validate it's reasonable
    if not regular_price:
        for sel in ["span[class*='productLinePriceOld']", "[class*='price'] [class*='old']", "span[class*='old-price']", "[class*='price-old']"]:
            el = soup.select_one(sel)
            if el:
                price_text = el.get_text(strip=True)
                m = re.search(r"([\d\s]+)", price_text.replace(" ", "").replace(" ", ""))
                if m:
                    candidate = m.group(1).replace(" ", "")
                    # Only use if it's >= sale price and not absurdly high (like 18500 for a 668 item)
                    if sale_price and int(candidate) >= int(sale_price) and int(candidate) <= int(sale_price) * 3:
                        regular_price = candidate
                    break

    # Format price: "sale (regular)" if both found, otherwise just sale
    if sale_price and regular_price:
        price = f"{sale_price} ({regular_price})"
    elif sale_price:
        price = sale_price
    elif regular_price:
        price = regular_price

    # Image: WB serves from basket-XX.wbbasket.ru
    image = None
    og_image = _meta_content(soup, property="og:image") or _meta_content(soup, name="og:image")
    if og_image and _is_valid_image_url(_abs(og_image, url)):
        image = _abs(og_image, url)
    else:
        # Also try to find images/big URLs directly
        for sel in ["img[src*='images/big']", "img[src*='basket-'][src*='wbbasket'][src*='images']", "img[src*='images'][src*='webp']"]:
            img = soup.select_one(sel)
            if img:
                src = img.get("src") or img.get("data-src") or img.get("data-src-pb")
                if src and _is_valid_image_url(_abs(src, url)):
                    image = _abs(src, url)
                    break

    # Fix WB thumbnail URL to get full-size image
    # WB uses /c246x328/ in path for thumbnails, replace with /orig/
    # Don't replace /big/ as it may be the best available for some products
    if image:
        image = re.sub(r'/c\d+x\d+/', '/orig/', image)

    return {"title": title, "price": price, "image": image, "link": url, "domain": "wildberries.ru"}


async def _parse_wb_html_fast(url: str) -> dict | None:
    """Быстрый парсинг WB через HTTP + BeautifulSoup (без Playwright)."""
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=httpx.Timeout(15.0)
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html_content = resp.text
    except Exception as e:
            log.debug("WB fast HTML fetch failed for %s: %s", url, e)
            return None

    return _parse_wb_html(html_content, url)


async def _parse_ozon_windows_cdp(url: str) -> dict | None:
    """Парсит Ozon на Windows через Edge CDP."""
    import asyncio
    import os
    import tempfile, socket, subprocess
    import json
    from playwright.async_api import async_playwright
    
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge_path = None
    for p in edge_paths:
        if os.path.exists(p):
            edge_path = p
            break
    if not edge_path:
        log.debug("Edge not found, skipping CDP fetch")
        return None

    def free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    cdp_port = free_port()
    edge_proc = subprocess.Popen([
        edge_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={tempfile.mkdtemp(prefix='ozon-edge-')}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ])

    try:
        await asyncio.sleep(3)
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            context = browser.contexts[0]
            page = await context.new_page()

            # Go to main page to establish session
            await page.goto("https://www.ozon.ru", wait_until="domcontentloaded", timeout=30000)
            # Wait for challenge to pass (like Ozon article suggests: 2-3 sec, but give more time)
            for i in range(40):
                title = await page.title()
                if "Captcha" in title:
                    print(f"DEBUG: Captcha detected at attempt {i+1}")
                    break
                if "нет соединения" in title:
                    print(f"DEBUG: Challenge page at attempt {i+1}")
                    break
                ready = await page.evaluate("document.querySelectorAll('[id^=\"state-\"]').length")
                if ready and "Antibot" not in title:
                    print(f"DEBUG: Challenge passed at attempt {i+1}")
                    break
                await page.wait_for_timeout(1000)
            else:
                print("DEBUG: Challenge timeout")

            await page.wait_for_timeout(3000)

            # Extract product ID from URL (handle short URLs by navigating in browser)
            product_id = None
            final_url = url
            
            # First try to extract from full product URL - match product ID at end of path
            # Pattern: /product/...-<id>/ or /product/...-<id>? or /product/<id>/
            m = re.search(r"/product/.*[-_](\d+)(?:/|\?)", final_url)
            if m:
                product_id = m.group(1)
            else:
                # Fallback: try to find any numeric ID at end of path
                m = re.search(r"/(\d+)(?:/|\?)", final_url)
                if m:
                    product_id = m.group(1)
            
            # If no product_id found and it's a short URL (ozon.ru/t/...), navigate in browser
            if not product_id and "/t/" in url:
                log.debug("Navigating to short URL in browser to resolve: %s", url)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Wait for redirect to complete and challenge to pass on product page
                for i in range(20):
                    await page.wait_for_timeout(1000)
                    title = await page.title()
                    if "Captcha" in title or "нет соединения" in title:
                        log.debug("Challenge on product page at attempt %d", i+1)
                        continue
                    break
                await page.wait_for_timeout(3000)
                final_url = page.url
                log.debug("Resolved short URL in browser: %s -> %s", url, final_url)
                # Extract product ID from resolved URL
                m = re.search(r"/product/.*[-_](\d+)(?:/|\?)", final_url)
                if m:
                    product_id = m.group(1)
                else:
                    m = re.search(r"/(\d+)(?:/|\?)", final_url)
                    if m:
                        product_id = m.group(1)
            
            if not product_id:
                log.debug("Could not extract product ID from URL: %s", url)
                await browser.close()
                return None

            # Call internal API using the product ID
            api_url = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=%2Fproduct%2F{product_id}%2F"
            fetch_js = (
                "async ({url}) => {"
                "const res = await fetch(url, {"
                "method: 'GET',"
                "credentials: 'include',"
                "headers: {'Accept': 'application/json', 'Content-Type': 'application/json'}"
                "});"
                "let json = null;"
                "try { json = await res.json(); } catch (e) {}"
                "return {status: res.status, url: res.url, json};"
                "}"
            )
            result = await page.evaluate(fetch_js, {"url": f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=%2Fproduct%2F{product_id}%2F"})
            
            print(f"DEBUG: API status={result['status']}, has_json={bool(result['json'])}")
            
            await browser.close()
            
            if result["status"] != 200 or not result["json"]:
                log.debug("Ozon CDP API failed: status=%s", result["status"])
                return None

            # Parse widgetStates
            widget_states = result["json"].get("widgetStates", {})
            print(f"DEBUG: widget_states count={len(widget_states)}")

            # Title
            title = None
            for key, value in widget_states.items():
                if key.startswith("webProductHeading"):
                    try:
                        data = json.loads(value)
                        title = data.get("title")
                        if title:
                            break
                    except Exception:
                        pass

            # Price from webPrice (and variants like webPriceDecreasedCompact)
            card_price = None
            regular_price = None
            original_price = None
            # Collect all webPrice widgets, prefer the one with actual price data
            webprice_widgets = []
            for key, value in widget_states.items():
                if key.startswith("webPrice"):
                    try:
                        data = json.loads(value)
                        webprice_widgets.append((key, data))
                    except Exception:
                        pass

            # First, try to find a widget with actual numeric prices
            numeric_price_widget = None
            for key, data in webprice_widgets:
                card_price = data.get("cardPrice")
                regular_price = data.get("price")
                original_price = data.get("originalPrice")
                # Check if this widget has actual numeric prices
                has_numeric_price = any(v for v in [card_price, regular_price, original_price] if v and any(c.isdigit() for c in str(v)))
                if has_numeric_price:
                    numeric_price_widget = (key, data, card_price, regular_price, original_price)
                    break

            # If no widget with numeric prices, fall back to first widget (might have textRs only)
            if numeric_price_widget is None and webprice_widgets:
                key, data = webprice_widgets[0]
                card_price = data.get("cardPrice")
                regular_price = data.get("price")
                original_price = data.get("originalPrice")
                text_rs = data.get("textRs", [])
                if card_price is None and text_rs and isinstance(text_rs, list):
                    card_price = text_rs[0].get("content")
            else:
                key, data, card_price, regular_price, original_price = numeric_price_widget

            # Format price: "cardPrice (regularPrice)" like WB
            price_parts = []
            if card_price:
                card_clean = re.sub(r"[^\d]", "", card_price)
                price_parts.append(card_clean)
            if regular_price and regular_price != card_price:
                reg_clean = re.sub(r"[^\d]", "", regular_price)
                price_parts.append(reg_clean)
            # Skip original_price unless it's different from regular
            if original_price and original_price != regular_price:
                orig_clean = re.sub(r"[^\d]", "", original_price)
                price_parts.append(orig_clean)
            if len(price_parts) >= 2:
                price = f"{price_parts[0]} ({price_parts[1]})"
            elif len(price_parts) == 1:
                price = price_parts[0]
            else:
                price = None

            # Image from webGallery (higher quality) or webStickyProducts/webPdpGrid
            image = None
            for key, value in widget_states.items():
                if key.startswith("webGallery"):
                    try:
                        data = json.loads(value)
                        # Get first image from gallery (highest quality)
                        images = data.get("images", [])
                        if images:
                            img_url = images[0].get("src") or images[0].get("imageUrl") or images[0].get("url")
                            if img_url and img_url.startswith("http"):
                                # Remove size constraints like /c50/ for higher quality (like WB /orig/)
                                image = re.sub(r"/c\d+/", "/", img_url)
                                break
                    except Exception:
                        pass

            # Fallback to webStickyProducts or webPdpGrid
            if not image:
                for key, value in widget_states.items():
                    if key.startswith("webStickyProducts") or key.startswith("webPdpGrid"):
                        try:
                            data = json.loads(value)
                            img_url = data.get("coverImageUrl") or data.get("imageUrl") or data.get("image")
                            if img_url and img_url.startswith("http"):
                                # Remove size constraints
                                image = re.sub(r"/c\d+/", "/", img_url)
                                break
                        except Exception:
                            pass

            # Fallback to og:image
            if not image:
                pass

            return {"title": title, "price": price, "image": image, "link": url, "domain": "ozon.ru"}
    except Exception as e:
        log.debug("Ozon CDP fetch failed for %s: %s", url, e)
        import traceback
        traceback.print_exc()
        return None
    finally:
        try:
            edge_proc.terminate()
        except Exception:
            pass