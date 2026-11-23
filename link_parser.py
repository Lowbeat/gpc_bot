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




def _parse_wb_html(html_content: str, url: str) -> dict | None:
    """Парсит WB HTML контент (общая логика для CDP и fast HTML)."""
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
        "[data-testid='product-price-wallet']",
        "[data-testid='product-price']",
        "span[class*='productLinePriceWallet']",
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
    for sel in [
        "[data-testid='product-price-final']",
        "span[class*='productLinePriceNow']",
        "span[class*='productLinePriceDefault']",
        "span[class*='price-block__price']:not([class*='Wallet'])",
        "[class*='price-block'] span:not([class*='Wallet']):not([class*='Old'])",
    ]:
        el = soup.select_one(sel)
        if el:
            price_text = el.get_text(strip=True)
            m = re.search(r"([\d\s]+)", price_text.replace(" ", "").replace(" ", ""))
            if m:
                regular_price = m.group(1).replace(" ", "")
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
        for sel in ["img[src*='images/big']", "img[src*='basket-'][src*='wbbasket'][src*='images']", "img[src*='images'][src*='webp']"]:
            img = soup.select_one(sel)
            if img:
                src = img.get("src") or img.get("data-src") or img.get("data-src-pb")
                if src and _is_valid_image_url(_abs(src, url)):
                    image = _abs(src, url)
                    break

    # Fix WB thumbnail URL to get full-size image
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

    # WB: try API first, then fast HTML, then CDP
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
    url = m.group(0).rstrip(").,!'")
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
            m = re.search(r"([\d\s]+)", price_text.replace(" ", "").replace(" ", ""))
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
                import re
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
        image = re.sub(r'/c\d+x\d+/', '/orig/', image)

    return {"title": title, "price": price, "image": image, "link": url, "domain": "wildberries.ru"}
