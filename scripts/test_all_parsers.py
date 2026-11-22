#!/usr/bin/env python3
"""Test all marketplace parsers via fetch_page().

Run: python scripts/test_all_parsers.py
"""
import asyncio
import sys
sys.path.insert(0, '.')

from link_parser import fetch_page

TEST_URLS = {
    'WB': 'https://www.wildberries.ru/catalog/225120540/detail.aspx',
    'YM': 'https://market.yandex.ru/card/moyka-vysokogo-davleniya-bort-kex-3000/5939487160',
    'Ozon': 'https://www.ozon.ru/product/nabor-dlya-kitayskoy-kalligrafii-bez-chernil-mnogorazovaya-tkan-dlya-pisma-vodoy-svitok-6114965509/',
    'Ali': 'https://aliexpress.ru/item/1005013068124123.html',
}

async def test_all():
    print('Testing all marketplace parsers...')
    print('=' * 60)
    
    for name, url in TEST_URLS.items():
        try:
            result = await fetch_page(url)
            print(f'{name}: OK')
            print(f'  Title: {result.get("title")[:60] if result.get("title") else "N/A"}')
            print(f'  Price: {result.get("price")}')
            print(f'  Image: {result.get("image")[:100] if result.get("image") else "N/A"}')
        except Exception as e:
            print(f'{name}: ERROR - {e}')
        print()

if __name__ == '__main__':
    asyncio.run(test_all())
