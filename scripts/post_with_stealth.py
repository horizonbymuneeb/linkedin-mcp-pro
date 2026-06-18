#!/usr/bin/env python3
"""post_with_stealth.py — end-to-end LinkedIn post via Playwright + stealth.

Two modes:
  1. Profile mode (RECOMMENDED): if ~/.linkedin-mcp/profile/ exists, uses it
     as a Playwright persistent context. No cookie file needed.
  2. Cookie mode (FALLBACK): reads li_at from /etc/linkedin-mcp-pro/li_at
     and injects it as an HttpOnly cookie.

Profile mode is what you want long-term (after running bootstrap_session.sh
on your laptop). Cookie mode is for emergencies or first-time setup.

Usage:
  python3 scripts/post_with_stealth.py
  python3 scripts/post_with_stealth.py "Custom post text"
  python3 scripts/post_with_stealth.py --profile-only    # error if no profile
  python3 scripts/post_with_stealth.py --cookie-only     # ignore profile
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
COOKIE_PATH = os.environ.get("LINKEDIN_MCP_COOKIE_FILE", "/etc/linkedin-mcp-pro/li_at")
PROXY = os.environ.get("LINKEDIN_MCP_PROXY", "socks5://127.0.0.1:1080")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
SCREENSHOT_PREFIX = "/tmp/li_post"


def detect_mode(force: str | None) -> str:
    """Return 'profile' or 'cookie' based on availability and --profile-only/--cookie-only."""
    has_profile = Path(PROFILE_DIR).is_dir() and (Path(PROFILE_DIR) / "Local State").exists()
    has_cookie = Path(COOKIE_PATH).exists() or os.access(COOKIE_PATH, os.R_OK)

    if force == "profile":
        if not has_profile:
            sys.exit(f"\u274c --profile-only but no profile at {PROFILE_DIR}")
        return "profile"
    if force == "cookie":
        if not has_cookie:
            sys.exit(f"\u274c --cookie-only but no cookie at {COOKIE_PATH}")
        return "cookie"
    if has_profile:
        return "profile"
    if has_cookie:
        return "cookie"
    sys.exit(
        "\u274c No profile or cookie found.\n"
        f"  Profile expected at: {PROFILE_DIR}\n"
        f"  Cookie expected at:  {COOKIE_PATH}\n"
        "  Run scripts/bootstrap_session.sh on your laptop, or paste a li_at cookie."
    )


def read_cookie() -> str:
    p = Path(COOKIE_PATH)
    if p.exists() and os.access(p, os.R_OK):
        return p.read_text().strip()
    import subprocess
    r = subprocess.run(
        ["sudo", "cat", str(p)], capture_output=True, text=True, check=True
    )
    return r.stdout.strip()


async def post(post_text: str, mode: str) -> int:
    from playwright.async_api import async_playwright

    try:
        from playwright_stealth import Stealth
        stealth_lib = Stealth()
        has_stealth = True
    except ImportError:
        has_stealth = False
    print(f"mode: {mode} | stealth: {'yes' if has_stealth else 'no'}")

    async with async_playwright() as p:
        if mode == "profile":
            print(f"\u2192 launching chromium (persistent profile: {PROFILE_DIR})")
            ctx = await p.chromium.launch_persistent_context(
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
        else:
            cookie = read_cookie()
            print(f"\u2192 launching chromium (cookie mode, len={len(cookie)})")
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
            ctx = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=USER_AGENT,
                locale="en-US",
                timezone_id="Asia/Karachi",
                color_scheme="light",
            )
            await ctx.add_cookies([{
                "name": "li_at", "value": cookie,
                "domain": ".linkedin.com", "path": "/",
                "httpOnly": True, "secure": True, "sameSite": "None",
            }])
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
        await page.screenshot(path=f"{SCREENSHOT_PREFIX}_1_feed.png")

        body = await page.inner_text("body", timeout=5000)
        is_login = any(x in page.url for x in ("/login", "/signup", "authwall"))
        is_logged_in = "Start a post" in body or "My Network" in body
        if is_login or not is_logged_in:
            print(f"\u274c NOT logged in (url={page.url})")
            print("  Re-sync profile (scripts/bootstrap_session.sh) or refresh cookie.")
            await page.screenshot(path=f"{SCREENSHOT_PREFIX}_FAIL.png", full_page=True)
            await ctx.close()
            return 1

        if not post_text:
            print("\u2713 session health: OK (logged in, can post)")
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
    parser.add_argument("--profile-only", action="store_true", help="Require profile mode")
    parser.add_argument("--cookie-only", action="store_true", help="Require cookie mode")
    parser.add_argument("--check", action="store_true", help="Just verify login")
    args = parser.parse_args()

    force = "profile" if args.profile_only else ("cookie" if args.cookie_only else None)
    mode = detect_mode(force)

    if args.check:
        text = ""
    elif args.text:
        text = args.text
    else:
        text = DEFAULT_POST
        print("Using default post text (use --check to skip posting).")
    return asyncio.run(post(text, mode))


if __name__ == "__main__":
    sys.exit(main())
