#!/usr/bin/env python3
"""End-to-end LinkedIn post via Playwright + stealth + SOCKS proxy.

Standalone script — does NOT use the linkedin-mcp-pro server. Reads a
`li_at` cookie from /etc/linkedin-mcp-pro/li_at, navigates to the feed,
opens the composer, types the post, clicks Post, and verifies.

Useful for:
  - One-off posts without launching the MCP server
  - Sanity check that the SOCKS tunnel + cookie are still working
  - Reference implementation of how the linkedin-mcp-pro browser module
    uses Playwright under the hood

Usage:
  python3 scripts/post_with_stealth.py
  python3 scripts/post_with_stealth.py "Custom post text here"

Prerequisites:
  pip install playwright playwright-stealth
  python3 -m playwright install chromium
  /etc/linkedin-mcp-pro/li_at must contain a valid li_at cookie
  SOCKS5 proxy must be running on 127.0.0.1:1080 (see scripts/laptop-proxy.sh)
"""
import asyncio
import os
import sys
from pathlib import Path

DEFAULT_POST = """\ud83d\ude80 Open-sourced linkedin-mcp-pro v0.3.0

MCP server for posting to LinkedIn through persistent browser sessions \u2014 no Voyager HTTP API (which flags cookies), no headless scraping (which breaks on every UI change).

22 tools (12 read + 8 write + 2 stats), 179 tests passing, MIT licensed.

The interesting engineering: making headless Chromium pass LinkedIn's fingerprint check. v3 cookies bind to UA/viewport/timezone, so even valid tokens get rejected on UA mismatch.

github.com/horizonbymuneeb/linkedin-mcp-pro

#opensource #mcp #browser-automation"""

COOKIE_PATH = "/etc/linkedin-mcp-pro/li_at"
PROXY = "socks5://127.0.0.1:1080"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
SCREENSHOT_PREFIX = "/tmp/li_post"


def read_cookie() -> str:
    """Read the li_at cookie from the protected file."""
    p = Path(COOKIE_PATH)
    if p.exists() and os.access(p, os.R_OK):
        return p.read_text().strip()
    # Fallback: file is root-only, use sudo
    import subprocess
    r = subprocess.run(
        ["sudo", "cat", str(p)], capture_output=True, text=True, check=True
    )
    return r.stdout.strip()


async def main() -> int:
    post_text = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_POST

    cookie = read_cookie()
    print(f"cookie length: {len(cookie)}")

    try:
        from playwright_stealth import Stealth
        stealth_lib = Stealth()
        has_stealth = True
    except ImportError:
        has_stealth = False
    print(f"stealth: {'yes' if has_stealth else 'no (manual fallback)'}")

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        print(f"\u2192 launching chromium via {PROXY}")
        browser = await p.chromium.launch(
            headless=True,
            proxy={"server": PROXY},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=USER_AGENT,
            locale="en-US",
            timezone_id="Asia/Karachi",
            color_scheme="light",
        )
        await ctx.add_cookies([{
            "name": "li_at",
            "value": cookie,
            "domain": ".linkedin.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        }])
        print("\u2192 cookie injected")

        page = await ctx.new_page()
        if has_stealth:
            await stealth_lib.apply_stealth_async(page)
        else:
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                window.chrome = {runtime: {}, loadTimes: function(){}};
            """)

        print("\u2192 /feed/")
        try:
            await page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded", timeout=30000,
            )
        except Exception as e:
            print(f"  nav warn: {type(e).__name__}: {str(e)[:80]}")

        await page.wait_for_timeout(4000)
        print(f"  title: {await page.title()!r}")
        print(f"  url:   {page.url}")
        await page.screenshot(path=f"{SCREENSHOT_PREFIX}_1_feed.png")

        body = await page.inner_text("body", timeout=5000)
        is_login = "/login" in page.url or "/signup" in page.url or "authwall" in page.url
        ok = ("Muneeb" in body or "muneeb" in body.lower()) and "Start a post" in body
        if is_login or not ok:
            print("\u274c NOT logged in \u2014 cookie flagged or invalid")
            await page.screenshot(path=f"{SCREENSHOT_PREFIX}_FAIL_login.png", full_page=True)
            await browser.close()
            return 1

        print("\u2192 click 'Start a post'")
        for sel in [
            'button:has-text("Start a post")',
            '[aria-label*="Start a post" i]',
            'div:has-text("Start a post")',
        ]:
            try:
                await page.locator(sel).first.click(timeout=4000)
                break
            except Exception:
                continue
        else:
            print("\u274c Start a post button not found")
            await page.screenshot(path=f"{SCREENSHOT_PREFIX}_FAIL_nocomposer.png", full_page=True)
            await browser.close()
            return 1

        await page.wait_for_timeout(2500)
        await page.screenshot(path=f"{SCREENSHOT_PREFIX}_2_composer.png")

        print(f"\u2192 typing {len(post_text)} chars")
        for sel in [
            'div[contenteditable="true"][role="textbox"]',
            'div[contenteditable="true"][aria-label*="post" i]',
            'div[contenteditable="true"]',
        ]:
            try:
                ed = page.locator(sel).first
                await ed.click(timeout=3000)
                await ed.fill(post_text)
                break
            except Exception:
                continue
        else:
            print("\u274c editor not found")
            await page.screenshot(path=f"{SCREENSHOT_PREFIX}_FAIL_noeditor.png", full_page=True)
            await browser.close()
            return 1

        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{SCREENSHOT_PREFIX}_3_typed.png")

        print("\u2192 click Post")
        for sel in [
            'button.share-actions__primary-action',
            'button[class*="share-actions"][class*="primary"]',
            'div[role="dialog"] button:has-text("Post")',
        ]:
            try:
                await page.locator(sel).first.click(timeout=5000)
                break
            except Exception:
                continue
        else:
            print("\u274c Post button not found")
            await page.screenshot(path=f"{SCREENSHOT_PREFIX}_FAIL_nopost.png", full_page=True)
            await browser.close()
            return 1

        await page.wait_for_timeout(7000)
        await page.screenshot(path=f"{SCREENSHOT_PREFIX}_4_done.png", full_page=True)

        try:
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)
            body = await page.inner_text("body", timeout=5000)
            await page.screenshot(path=f"{SCREENSHOT_PREFIX}_5_verify.png", full_page=True)
            marker = "linkedin-mcp-pro v0.3.0"
            if marker in body:
                print(f"\u2705 SUCCESS \u2014 post visible in feed (matched {marker!r})")
            else:
                print("\u26a0\ufe0f posted but content not found in feed")
        except Exception as e:
            print(f"verify warn: {e}")

        await browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
