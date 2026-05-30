"""Prueba puntual: ¿Playwright recupera los SPAs que el fetch estático no pudo?
Compara links/markdown del HTML estático vs el renderizado con JS.
Uso: python -m scraper._test_playwright
"""
import asyncio

import httpx
from playwright.async_api import async_playwright

from scraper.discover import looks_like_development
from scraper.fetch import extract_internal_links, fetch_html, html_to_markdown

SITES = ["https://0celsius.mx", "https://atriahogar.com", "https://artila.mx"]


async def render(url: str) -> str | None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1500)
            return await page.content()
        except Exception as e:  # noqa: BLE001
            print(f"    playwright error: {e}")
            return None
        finally:
            await browser.close()


async def main():
    async with httpx.AsyncClient() as c:
        for url in SITES:
            print(f"\n=== {url} ===")
            static = await fetch_html(c, url)
            s_links = len(extract_internal_links(static, url)) if static else 0
            s_dev = len([l for l in extract_internal_links(static, url) if looks_like_development(l)]) if static else 0
            print(f"  ESTÁTICO : links={s_links}  desarrollo={s_dev}  md={len(html_to_markdown(static)) if static else 0}ch")

            rendered = await render(url)
            if not rendered:
                print("  PLAYWRIGHT: (sin contenido)")
                continue
            r_all = extract_internal_links(rendered, url)
            r_dev = [l for l in r_all if looks_like_development(l)]
            print(f"  PLAYWRIGHT: links={len(r_all)}  desarrollo={len(r_dev)}  md={len(html_to_markdown(rendered))}ch")
            for l in sorted(r_dev)[:8]:
                print(f"     → {l}")


if __name__ == "__main__":
    asyncio.run(main())
