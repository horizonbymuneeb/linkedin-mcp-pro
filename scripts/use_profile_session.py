#!/usr/bin/env python3
"""use_profile_session.py — post to LinkedIn using a persistent Chrome profile.

This is the production-recommended way to run linkedin-mcp-pro: instead of
extracting a fresh `li_at` cookie every few days, sync your real laptop's
Chrome profile (with `scripts/bootstrap_session.sh`) and use that here.

Why this is better:
  - Cookie lifecycle: days → 6-12 months
  - No "cookie flagged" pain
  - LinkedIn sees a consistent browser session (real UA, real fingerprint,
    same cookies over time)
  - Auto-refresh: the cookie is updated by LinkedIn itself on each page load

Usage:
  # On EC2, after running bootstrap_session.sh on your laptop
  python3 scripts/use_profile_session.py

  # With explicit post text
  python3 scripts/use_profile_session.py "My post text here"

  # No post — just check session health
  python3 scripts/use_profile_session.py --check
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

DEFAULT_POST = """\ud83d\ude80 Open-sourced linkedin-mcp-pro v0.4.0

MCP server for posting to LinkedIn through a persistent browser session \u2014 sync your real Chrome profile once, post for months without re-auth.

The auth story: bootstrap on laptop (Chrome profile copy), server uses it via Playwright. No cookie files, no API key rotation, no Voyager calls.

22 tools (12 read + 8 write + 2 stats), 179 tests, MIT licensed.

github.com/horizonbymuneeb/linkedin-mcp-pro

#opensource #mcp #browser-automation"""

PROFILE_DIR = os.environ.get("LINKEDIN_MCP_PROFILE_DIR", "/home/admin/.linkedin-mcp/profile")
PROXY = os.environ.get("LINKEDIN_MCP_PROXY", "socks5://127.0.0.1:1080")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
SCREENSHOT_PREFIX = "/tmp/li_profile_post"


def check_profile() -> bool:
    """Verify the profile directory has the minimum required files."""
    p = Path(PROFILE_DIR)
    if not p.is_dir():
        print(f"\u274c Profile dir missing: {PROFILE_DIR}")
        print("  Run scripts/cookie_to_profile.py or scripts/bootstrap_session.sh first.")
        return False
    # Two layout options, both supported:
    # 1. state.json (from cookie_to_profile.py, more reliable for HttpOnly cookies)
    # 2. Default/Cookies DB (from bootstrap_session.sh / linkedin-mcp login)
    state_json = p / "storage_state.json"
    if state_json.is_file():
        size = state_json.stat().st_size
        print(f"\u2713 Profile OK (storage_state): {state_json} ({size:,} bytes)")
        return True
    cookies_db = p / "Default" / "Cookies"
    if cookies_db.exists():
        size = cookies_db.stat().st_size
        print(f"\u2713 Profile OK (persistent): {cookies_db} ({size:,} bytes)")
        return True
    print(f"\u274c Profile incomplete: no state.json or Default/Cookies in {PROFILE_DIR}")
    return False


async def post_via_profile(post_text: str) -> int:
    from playwright.async_api import async_playwright

    try:
        from playwright_stealth import Stealth
        stealth_lib = Stealth()
        has_stealth = True
    except ImportError:
        has_stealth = False
    print(f"stealth: {'yes' if has_stealth else 'no (manual fallback)'}")

    state_json = Path(PROFILE_DIR) / "storage_state.json"
    use_storage_state = state_json.is_file()
    print(f"profile mode: {'storage_state' if use_storage_state else 'persistent context'}")

    async with async_playwright() as p:
        print(f"\u2192 launching chromium")
        browser = await p.chromium.launch(
            headless=True,
            proxy={"server": PROXY} if PROXY else None,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        if use_storage_state:
            # state.json approach (more reliable for HttpOnly cookies)
            ctx = await browser.new_context(
                storage_state=str(state_json),
                viewport={"width": 1920, "height": 1080},
                user_agent=USER_AGENT,
                locale="en-US",
                timezone_id="Asia/Karachi",
                color_scheme="light",
            )
        else:
            # persistent context approach (from bootstrap_session.sh / linkedin-mcp login)
            ctx = await browser.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=True,
                proxy={"server": PROXY} if PROXY else None,
                viewport={"width": 1920, "height": 1080},
                user_agent=USER_AGENT,
                locale="en-US",
                timezone_id="Asia/Karachi",
                color_scheme="light",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

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
        is_login = any(x in page.url for x in ("/login", "/signup", "authwall"))
        # Accept any logged-in indicator — could be your name, "Start a post", etc.
        is_logged_in = "Start a post" in body or "My Network" in body
        if is_login or not is_logged_in:
            print("\u274c NOT logged in \u2014 profile may need re-sync")
            print("  Re-run scripts/bootstrap_session.sh on your laptop.")
            await page.screenshot(path=f"{SCREENSHOT_PREFIX}_FAIL.png", full_page=True)
            await ctx.close()
            return 1

        if not post_text:
            print("\u2713 Session health: OK (logged in, can post)")
            await ctx.close()
            return 0

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
            await ctx.close()
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
            await ctx.close()
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
            await ctx.close()
            return 1

        await page.wait_for_timeout(7000)
        await page.screenshot(path=f"{SCREENSHOT_PREFIX}_4_done.png", full_page=True)
        await ctx.close()
        print("\u2705 posted")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text", nargs="?", help="Post text (omit for health check)")
    parser.add_argument("--check", action="store_true", help="Check session health only")
    args = parser.parse_args()

    if not check_profile():
        return 1

    if args.check:
        text = ""
    elif args.text:
        text = args.text
    else:
        text = DEFAULT_POST
        print("Using default post text (run with --check to skip posting).")
    return asyncio.run(post_via_profile(text))


if __name__ == "__main__":
    sys.exit(main())
