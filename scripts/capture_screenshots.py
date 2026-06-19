#!/usr/bin/env python3
"""Capture screenshots of all 11 panels using Playwright.

Run while the web server is up on :8000:
    python3 -m linkedin_mcp.web          # in one terminal
    python3 scripts/capture_screenshots.py   # in another
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

PANELS = [
    "llm", "safety", "engagement", "audit", "schedules",
    "templates", "drafts", "analytics", "install", "profile", "settings",
]
BASE = "http://127.0.0.1:8000/static"
OUT = Path("docs/screenshots")
OUT.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for theme in ("light", "dark"):
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
            )
            page = await context.new_page()
            for panel in PANELS:
                await page.goto(f"{BASE}/{panel}.html", wait_until="networkidle")
                if theme == "dark":
                    await page.evaluate(
                        "document.documentElement.classList.add('dark');"
                        " localStorage.setItem('theme', 'dark');"
                    )
                else:
                    await page.evaluate(
                        "document.documentElement.classList.remove('dark');"
                        " localStorage.setItem('theme', 'light');"
                    )
                await page.wait_for_timeout(500)
                out_path = OUT / f"{panel}-{theme}.png"
                await page.screenshot(path=str(out_path), full_page=True)
                print(f"  ✓ {out_path.name}")
            await context.close()
        await browser.close()
        print(f"Done: {len(PANELS) * 2} screenshots saved to {OUT}/")


if __name__ == "__main__":
    asyncio.run(main())
