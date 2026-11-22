import asyncio, sys, os, tempfile, socket, subprocess, json, re
sys.path.insert(0, r"D:\Projects\gpc_bot")

async def test():
    url = "https://www.ozon.ru/product/nabor-dlya-kitayskoy-kalligrafii-bez-chernil-mnogorazovaya-tkan-dlya-pisma-vodoy-svitok-6114965509/"
    
    edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    
    def free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    
    cdp_port = free_port()
    profile = tempfile.mkdtemp(prefix="ozon-edge-")
    
    edge_proc = subprocess.Popen([
        edge_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={tempfile.mkdtemp(prefix='ozon-edge-')}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ])
    
    await asyncio.sleep(3)
    
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            context = browser.contexts[0]
            page = await context.new_page()
            
            await page.goto("https://www.ozon.ru", wait_until="domcontentloaded", timeout=30000)
            
            for i in range(40):
                title = await page.title()
                if "Captcha" in title:
                    print("Captcha")
                    break
                if "нет соединения" in title:
                    print("Challenge")
                    break
                ready = await page.evaluate('document.querySelectorAll(\'[id^="state-"]\').length')
                if ready and "Antibot" not in title:
                    break
                await page.wait_for_timeout(1000)
            else:
                print("TIMEOUT")
            
            await page.wait_for_timeout(3000)
            
            result = await page.evaluate("""async ({url}) => {
                const res = await fetch(url, {
                    method: "GET",
                    credentials: "include",
                    headers: {"Accept": "application/json", "Content-Type": "application/json"}
                });
                let json = null;
                try { json = await res.json(); } catch (e) {}
                return {status: res.status, url: res.url, json};
            }""", {"url": "https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=%2Fproduct%2F6114965509%2F"})
            
            if result["json"]:
                widget_states = result["json"].get("widgetStates", {})
                
                # Print ALL widget keys that contain "price" (case insensitive)
                for key, value in widget_states.items():
                    if "price" in key.lower():
                        print(f"=== {key} ===")
                        try:
                            data = json.loads(value)
                            print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
                        except:
                            print(f"  Raw: {value[:500]}")
                        print()
            
            await browser.close()
    finally:
        edge_proc.terminate()

asyncio.run(test())
