#!/usr/bin/env python3
"""cookie_to_profile.py — bootstrap a persistent browser session from a single li_at cookie.

The QUICK path to a self-sufficient setup. Instead of running
bootstrap_session.sh on your laptop, you can build the session right on the
server from the cookie you already have.

What this does:
  1. Read li_at from /etc/linkedin-mcp-pro/li_at (or --cookie flag)
  2. Launch a regular Chromium browser (NOT persistent context — see notes)
  3. Inject the li_at cookie as HttpOnly + SameSite=None
  4. Navigate to https://www.linkedin.com/feed/
  5. Wait for LinkedIn's JS to populate: ~30 cookies (JSESSIONID, bcookie,
     lidc, li_sugr, liap, ...) + localStorage
  6. Export the FULL session state (all cookies + storage) to
     ~/.linkedin-mcp/profile/state.json using Playwright's storage_state API
  7. Close the browser

After this, scripts/use_profile_session.py and scripts/post_with_stealth.py
will use the state.json automatically (no more cookie file needed).

Why storage_state.json instead of launch_persistent_context?
  Playwright's launch_persistent_context has a known quirk: cookies added via
  ctx.add_cookies() are NOT reliably persisted to the on-disk Cookies DB.
  The Cookies DB may end up with empty value blobs and the HttpOnly auth
  cookie (li_at) is sometimes missing entirely on next launch. The
  storage_state.json API is the OFFICIAL Playwright way to persist sessions
  and works correctly with HttpOnly cookies.

Usage:
  # Standard (reads from /etc/linkedin-mcp-pro/li_at)
  python3 scripts/cookie_to_profile.py

  # Inline cookie
  python3 scripts/cookie_to_profile.py --cookie "AQEDAS27ghk..."

  # Different profile path
  python3 scripts/cookie_to_profile.py --profile-dir /tmp/test-profile

  # Force overwrite existing profile
  python3 scripts/cookie_to_profile.py --force

  # Different proxy (e.g. residential service)
  python3 scripts/cookie_to_profile.py --proxy "socks5://user:pass@proxy.example.com:1080"

Prerequisites:
  pip install playwright playwright-stealth
  python3 -m playwright install chromium

Notes:
  - This script does NOT eliminate the need for a proxy (laptop/phone/residential).
    LinkedIn still blocks datacenter IPs. But it DOES eliminate the need to
    manually extract and paste cookies — the profile handles refreshes.
  - If LinkedIn shows a captcha or 2FA, the script will fail (cookie alone
    can't solve those). Re-extract a fresh cookie and retry.
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

DEFAULT_COOKIE_PATH = "/etc/linkedin-mcp-pro/li_at"
DEFAULT_PROFILE_DIR = "/home/admin/.linkedin-mcp/profile"
DEFAULT_PROXY = "socks5://127.0.0.1:1080"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


def read_cookie(path: str = DEFAULT_COOKIE_PATH, inline: str | None = None) -> str:
    if inline:
        return inline.strip()
    p = Path(path)
    try:
        return p.read_text().strip() if p.is_file() else ""
    except (PermissionError, OSError):
        pass
    import subprocess
    r = subprocess.run(["sudo", "cat", str(p)], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def detect_login_state(body: str, url: str) -> tuple[bool, str]:
    """Return (is_logged_in, reason)."""
    if any(x in url for x in ("/login", "/signup", "authwall", "/checkpoint")):
        return False, f"redirected to {url}"
    if "Start a post" in body:
        return True, "'Start a post' visible (composer button)"
    if "My Network" in body:
        return True, "feed nav bar present"
    if "feed" in url and len(body) > 1000:
        return True, "feed page loaded (no explicit indicator, but content present)"
    return False, "no logged-in indicator found in page"


async def build_profile(cookie: str, profile_dir: str, proxy: str) -> int:
    from playwright.async_api import async_playwright

    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
        has_stealth = True
    except ImportError:
        has_stealth = False

    pp = Path(profile_dir)
    if pp.exists() and any(pp.iterdir()):
        sys.exit(
            f"\u274c {profile_dir} already exists and is non-empty.\n"
            "  Re-run with --force to overwrite, or pick a different --profile-dir."
        )
    pp.mkdir(parents=True, exist_ok=True)

    print(f"\u2192 profile dir: {profile_dir}")
    print(f"\u2192 proxy:       {proxy or '(direct — expect LinkedIn to block!)'}")
    print(f"\u2192 cookie len:  {len(cookie)}")
    print(f"\u2192 stealth:     {'yes' if has_stealth else 'no (manual fallback)'}")

    async with async_playwright() as p:
        print("\u2192 launching chromium (regular browser, will export state.json)")
        browser = await p.chromium.launch(
            headless=True,
            proxy={"server": proxy} if proxy else None,
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
        page = await ctx.new_page()

        if has_stealth:
            await stealth.apply_stealth_async(page)
        else:
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                window.chrome = {runtime: {}, loadTimes: function(){}};
            """)

        # Inject cookie BEFORE any LinkedIn navigation
        await ctx.add_cookies([{
            "name": "li_at",
            "value": cookie,
            "domain": ".linkedin.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        }])
        print("\u2192 li_at cookie injected")

        # Warm up with a lightweight endpoint
        print("\u2192 /li/track (warm-up)...")
        try:
            await page.goto(
                "https://www.linkedin.com/li/track",
                wait_until="domcontentloaded", timeout=20000,
            )
        except Exception as e:
            print(f"  warm-up warn: {type(e).__name__}: {str(e)[:60]}")

        # Full feed load
        print("\u2192 /feed/ (full load + wait for JS to populate)...")
        try:
            await page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded", timeout=30000,
            )
        except Exception as e:
            print(f"  feed warn: {type(e).__name__}: {str(e)[:60]}")

        # Give LinkedIn's JS time to set additional cookies, localStorage, etc.
        print("\u2192 waiting 8s for LinkedIn JS to populate cookies + storage...")
        await page.wait_for_timeout(8000)

        title = await page.title()
        url = page.url
        print(f"  title: {title!r}")
        print(f"  url:   {url}")

        try:
            body = await page.inner_text("body", timeout=5000)
        except Exception:
            body = ""

        is_logged_in, reason = detect_login_state(body, url)
        print(f"  login check: {is_logged_in} ({reason})")

        # Take a screenshot for verification
        shot = "/tmp/cookie_to_profile_result.png"
        try:
            await page.screenshot(path=shot, full_page=False)
            print(f"  screenshot: {shot}")
        except Exception:
            pass

        # Count cookies in-memory before export
        cookies = await ctx.cookies()
        linkedin_cookies = [c for c in cookies if "linkedin" in c.get("domain", "")]
        has_li_at = any(c.get("name") == "li_at" for c in linkedin_cookies)
        print(f"  cookies in-memory: {len(cookies)} total, {len(linkedin_cookies)} linkedin")
        print(f"  li_at present:     {has_li_at}")

        if not is_logged_in:
            print("\n\u274c login failed \u2014 not exporting state")
            await ctx.close()
            await browser.close()
            shutil.rmtree(profile_dir, ignore_errors=True)
            print("  re-extract a fresh li_at cookie and retry.")
            print("  common causes:")
            print("    - cookie already flagged/expired")
            print("    - proxy IP is blocked (try different proxy)")
            print("    - LinkedIn is showing a challenge (cookie alone can't solve)")
            return 1

        # Export the FULL session state to state.json
        state_path = pp / "state.json"
        state = await ctx.storage_state(path=str(state_path))
        print(f"\n\u2192 exported state to {state_path}")
        with open(state_path) as f:
            saved = json.load(f)
        saved_linkedin = [c for c in saved.get("cookies", []) if "linkedin" in c.get("domain", "")]
        saved_li_at = any(c.get("name") == "li_at" for c in saved_linkedin)
        print(f"  state.json size:    {state_path.stat().st_size:,} bytes")
        print(f"  saved cookies:      {len(saved.get('cookies', []))} total, {len(saved_linkedin)} linkedin")
        print(f"  li_at in state:     {saved_li_at}")
        print(f"  origins (localStorage): {len(saved.get('origins', []))}")

        await ctx.close()
        await browser.close()

    if not saved_li_at:
        print("\n\u26a0\ufe0f WARNING: li_at not in saved state \u2014 profile may not work next time")
        print("  Try again or check LinkedIn's response.")

    print(f"\n\u2705 profile built \u2014 {len(saved_linkedin)} LinkedIn cookies saved")
    print(f"  location: {profile_dir}/state.json")
    print()
    print("Next steps:")
    print("  python3 scripts/use_profile_session.py --check   # verify it works")
    print("  python3 scripts/post_with_stealth.py            # post (auto-uses profile)")
    print()
    print("Re-run this script if LinkedIn forces a re-auth (every 6-12 months).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a persistent browser session from a single li_at cookie.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--cookie", help="li_at cookie value (default: read from file)")
    parser.add_argument("--cookie-file", default=DEFAULT_COOKIE_PATH,
                        help=f"path to li_at file (default: {DEFAULT_COOKIE_PATH})")
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR,
                        help=f"profile destination (default: {DEFAULT_PROFILE_DIR})")
    parser.add_argument("--proxy", default=DEFAULT_PROXY,
                        help=f"SOCKS/HTTP proxy (default: {DEFAULT_PROXY})")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing profile dir")
    args = parser.parse_args()

    cookie = read_cookie(args.cookie_file, args.cookie)
    if not cookie or len(cookie) < 50:
        sys.exit(f"\u274c cookie looks too short (len={len(cookie)}). paste the full li_at value.")

    if args.force:
        pp = Path(args.profile_dir)
        if pp.exists():
            print(f"--force: removing {args.profile_dir}")
            shutil.rmtree(args.profile_dir, ignore_errors=True)

    return asyncio.run(build_profile(cookie, args.profile_dir, args.proxy))


if __name__ == "__main__":
    sys.exit(main())
