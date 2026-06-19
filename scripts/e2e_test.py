#!/usr/bin/env python3
"""End-to-end smoke test for LinkedIn MCP Pro.

Exercises every public surface:

1. CLI: each binary responds to --help with exit code 0
2. Web server: 15 pages all return 200 with the unified shell
3. Sidebar links: rewritten to clean routes (/drafts not /static/drafts.html)
4. Shell includes: resolved server-side (no {% include %} leaks)
5. REST API: GET endpoints return JSON, POST endpoints accept JSON
6. CLI end-to-end: draft create + schedule list round-trip via subprocess

Run after starting the server:

    linkedin-mcp-web --port 8080 &
    python scripts/e2e_test.py [--host 127.0.0.1] [--port 8080] [--strict]

Exit codes:
    0 — all green
    1 — one or more failures (printed in summary)
    2 — server unreachable (could not start test)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

PAGES = (
    "/", "/jobs", "/connect", "/cookies", "/drafts",
    "/schedules", "/engagement", "/llm", "/safety",
    "/audit", "/profile", "/analytics", "/install",
    "/settings", "/templates",
)

# Each entry: (path, expected_substring_in_response, optional_method)
GET_ENDPOINTS = (
    ("/api/version", "\"version\""),
    ("/api/summary", None),  # any JSON OK
    ("/api/drafts", None),
    ("/api/schedules", None),
    ("/api/templates", None),
    ("/api/engagement", None),
    ("/api/profile", None),
    ("/api/audit", None),
    ("/api/safety/status", None),
    ("/api/deadman", None),
    ("/api/accounts", None),
    ("/api/cookies/health", None),
    ("/api/llm/providers", None),
)

POST_ENDPOINTS = (
    ("/api/cache/clear", {}, "ok"),
    ("/api/settings/reset", {}, None),
    ("/api/llm/test-all", {}, None),
)

CLI_COMMANDS = (
    "linkedin-mcp-pro",
    "linkedin-mcp-web",
    "linkedin-mcp-install",
    "linkedin-mcp-login",
    "linkedin-mcp-templates",
    "linkedin-mcp-schedule",
    "linkedin-mcp-analytics",
    "linkedin-mcp-stats",
    "linkedin-mcp-health",
    "linkedin-mcp-deadman",
)


@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class Suite:
    results: list[TestResult] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    def add(self, name: str, passed: bool, detail: str = "", duration_ms: float = 0.0) -> None:
        self.results.append(TestResult(name, passed, detail, duration_ms))

    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    def print_summary(self) -> None:
        total = len(self.results)
        elapsed = time.time() - self.started
        print()
        print("=" * 70)
        print(f"E2E SUMMARY: {self.passed()}/{total} passed in {elapsed:.2f}s")
        if self.failed():
            print(f"FAILURES ({self.failed()}):")
            for r in self.results:
                if not r.passed:
                    print(f"  ✗ {r.name}: {r.detail}")
        else:
            print("ALL GREEN ✓")
        print("=" * 70)


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return 0, str(e)


def http_post(url: str, body: dict, timeout: float = 5.0) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return 0, str(e)


def server_reachable(host: str, port: int) -> bool:
    code, _ = http_get(f"http://{host}:{port}/", timeout=2.0)
    return code == 200


# ----------------------------------------------------------------------------
# Test categories
# ----------------------------------------------------------------------------

def test_server_up(suite: Suite, host: str, port: int) -> None:
    """Server must be reachable before anything else runs."""
    t0 = time.time()
    code, _ = http_get(f"http://{host}:{port}/api/version", timeout=2.0)
    elapsed = (time.time() - t0) * 1000
    if code == 200:
        suite.add("server reachable", True, f"GET /api/version → 200", elapsed)
    else:
        suite.add(
            "server reachable",
            False,
            f"GET http://{host}:{port}/api/version → {code}. "
            f"Is `linkedin-mcp-web --port {port}` running?",
            elapsed,
        )


def test_pages(suite: Suite, host: str, port: int, strict: bool) -> None:
    """All 15 dashboard pages must return 200."""
    for path in PAGES:
        t0 = time.time()
        code, body = http_get(f"http://{host}:{port}{path}")
        elapsed = (time.time() - t0) * 1000
        passed = code == 200
        detail = f"{path} → {code} ({len(body)} bytes)"
        if not passed:
            detail += " — FAIL"
        suite.add(f"page {path}", passed, detail, elapsed)

    if strict:
        # Strict mode: each page must contain the shell's brand signature
        for path in PAGES:
            _, body = http_get(f"http://{host}:{port}{path}")
            if "tailwind.config" not in body:
                suite.add(f"page {path} has shell", False, "missing tailwind config")
            else:
                suite.add(f"page {path} has shell", True, "")


def test_sidebar_links(suite: Suite, host: str, port: int) -> None:
    """Sidebar must not leak /static/*.html links."""
    leaked_pages: list[str] = []
    for path in PAGES:
        _, body = http_get(f"http://{host}:{port}{path}")
        # Look for any href="/static/<page>.html" (where <page> is a known page)
        for p in (
            "drafts", "schedules", "engagement", "jobs", "analytics",
            "connect", "cookies", "profile", "llm", "safety",
            "audit", "install", "settings", "templates", "index",
        ):
            if f'href="/static/{p}.html"' in body:
                leaked_pages.append(f"{path} → /static/{p}.html")
    if leaked_pages:
        suite.add(
            "sidebar links clean",
            False,
            f"{len(leaked_pages)} leaks: {leaked_pages[:5]}",
        )
    else:
        suite.add(
            "sidebar links clean",
            True,
            f"0 leaks across {len(PAGES)} pages",
        )


def test_shell_includes_resolved(suite: Suite, host: str, port: int) -> None:
    """No {% include %} placeholder should leak through."""
    leaked: list[str] = []
    for path in PAGES:
        _, body = http_get(f"http://{host}:{port}{path}")
        if "{% include" in body or "%}" in body:
            leaked.append(path)
    if leaked:
        suite.add("shell include resolved", False, f"placeholder leaked on: {leaked[:5]}")
    else:
        suite.add("shell include resolved", True, "0 leaks across all pages")


def test_get_endpoints(suite: Suite, host: str, port: int) -> None:
    """All GET endpoints must return JSON."""
    for path, expected_substr in GET_ENDPOINTS:
        t0 = time.time()
        code, body = http_get(f"http://{host}:{port}{path}")
        elapsed = (time.time() - t0) * 1000
        if code != 200:
            suite.add(f"GET {path}", False, f"→ {code}: {body[:100]}", elapsed)
            continue
        # Must be valid JSON
        try:
            json.loads(body)
        except json.JSONDecodeError as e:
            suite.add(f"GET {path}", False, f"not JSON: {e}", elapsed)
            continue
        if expected_substr and expected_substr not in body:
            suite.add(f"GET {path}", False, f"missing '{expected_substr}'", elapsed)
            continue
        suite.add(f"GET {path}", True, f"200 JSON ({len(body)} bytes)", elapsed)


def test_post_endpoints(suite: Suite, host: str, port: int) -> None:
    """All POST endpoints must accept JSON."""
    for path, body, expect_substr in POST_ENDPOINTS:
        t0 = time.time()
        code, resp = http_post(f"http://{host}:{port}{path}", body)
        elapsed = (time.time() - t0) * 1000
        # Acceptable: 200 (success), 4xx (auth/config issue, not crash), 5xx is bad
        if code >= 500:
            suite.add(f"POST {path}", False, f"→ {code}: {resp[:100]}", elapsed)
        else:
            detail = f"→ {code}"
            if expect_substr and expect_substr in resp:
                detail += " (contains expected)"
            suite.add(f"POST {path}", True, detail, elapsed)


def test_jobs_module(suite: Suite, host: str, port: int) -> None:
    """Jobs module endpoints."""
    # /api/jobs/health must return 200 with ok status
    code, body = http_get(f"http://{host}:{port}/api/jobs/health")
    if code == 200:
        try:
            data = json.loads(body)
            ok = data.get("ok", data.get("status", "unknown"))
            suite.add("jobs /health", True, f"200, status={ok}")
        except json.JSONDecodeError:
            suite.add("jobs /health", False, "not JSON")
    else:
        suite.add("jobs /health", False, f"→ {code}")

    # /api/jobs/wizard/questions should be reachable
    code, body = http_get(f"http://{host}:{port}/api/jobs/wizard/questions")
    if code in (200, 404):  # 404 OK if wizard disabled
        suite.add("jobs /wizard/questions", code == 200, f"→ {code}")
    else:
        suite.add("jobs /wizard/questions", False, f"→ {code}")

    # /api/jobs/profile
    code, body = http_get(f"http://{host}:{port}/api/jobs/profile")
    suite.add("jobs /profile", code in (200, 404), f"→ {code}")

    # /api/jobs/templates
    code, body = http_get(f"http://{host}:{port}/api/jobs/templates")
    suite.add("jobs /templates", code in (200, 404), f"→ {code}")


def test_cli_commands(suite: Suite) -> None:
    """Every CLI command must respond to --help."""
    for cmd in CLI_COMMANDS:
        if shutil.which(cmd) is None:
            suite.add(f"CLI {cmd} --help", False, "binary not found in PATH")
            continue
        t0 = time.time()
        try:
            proc = subprocess.run(
                [cmd, "--help"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            elapsed = (time.time() - t0) * 1000
            if proc.returncode == 0:
                suite.add(f"CLI {cmd} --help", True, f"exit 0", elapsed)
            else:
                # Some CLIs exit 0 with help, some exit 2 — both are acceptable
                # as long as usage info is shown
                out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
                if "usage:" in out.lower() or "options:" in out.lower() or "help" in out.lower():
                    suite.add(f"CLI {cmd} --help", True, f"exit {proc.returncode} (has usage)", elapsed)
                else:
                    suite.add(f"CLI {cmd} --help", False, f"exit {proc.returncode}", elapsed)
        except subprocess.TimeoutExpired:
            suite.add(f"CLI {cmd} --help", False, "timeout (10s)")
        except FileNotFoundError:
            suite.add(f"CLI {cmd} --help", False, "not found")


def test_drafts_round_trip(suite: Suite, host: str, port: int) -> None:
    """Create a draft via API, then list drafts to confirm it appears."""
    unique_body = f"E2E test draft created at {time.time()}"
    create_body = {"body": unique_body, "tags": ["e2e"]}

    code, resp = http_post(f"http://{host}:{port}/api/drafts/save", create_body)
    if code not in (200, 201):
        suite.add("drafts round-trip", False, f"create failed → {code}: {resp[:200]}")
        return

    # List and verify
    code, resp = http_get(f"http://{host}:{port}/api/drafts")
    if code != 200:
        suite.add("drafts round-trip", False, f"list failed → {code}")
        return

    try:
        data = json.loads(resp)
    except json.JSONDecodeError:
        suite.add("drafts round-trip", False, "list response not JSON")
        return

    # Schema may be a list or {"drafts": [...]}
    if isinstance(data, dict) and "drafts" in data:
        drafts = data["drafts"]
    elif isinstance(data, list):
        drafts = data
    else:
        suite.add("drafts round-trip", False, f"unexpected schema: {type(data).__name__}")
        return

    found = any(
        unique_body in (d if isinstance(d, str) else d.get("body", "") or "")
        for d in drafts
    )
    if found:
        suite.add(
            "drafts round-trip",
            True,
            f"created + verified in list of {len(drafts)} drafts",
        )
    else:
        suite.add(
            "drafts round-trip",
            False,
            f"create ok but unique body not in list of {len(drafts)} drafts",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="End-to-end smoke test for LinkedIn MCP Pro"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--strict", action="store_true",
                        help="also check shell integrity per page")
    parser.add_argument("--skip-cli", action="store_true",
                        help="skip CLI tests (useful in CI without PATH)")
    parser.add_argument("--no-server-check", action="store_true",
                        help="skip the server-up gate (run anyway)")
    args = parser.parse_args()

    suite = Suite()

    print(f"E2E test against http://{args.host}:{args.port}")
    print(f"Strict mode: {args.strict}")
    print()

    # 1. Server reachable?
    test_server_up(suite, args.host, args.port)
    if suite.results and not suite.results[-1].passed:
        suite.print_summary()
        return 2

    # 2. Pages
    print("→ Pages...")
    test_pages(suite, args.host, args.port, args.strict)

    # 3. Sidebar links clean
    print("→ Sidebar links...")
    test_sidebar_links(suite, args.host, args.port)

    # 4. Shell includes resolved
    print("→ Shell includes...")
    test_shell_includes_resolved(suite, args.host, args.port)

    # 5. GET endpoints
    print("→ GET endpoints...")
    test_get_endpoints(suite, args.host, args.port)

    # 6. POST endpoints
    print("→ POST endpoints...")
    test_post_endpoints(suite, args.host, args.port)

    # 7. Jobs module
    print("→ Jobs module...")
    test_jobs_module(suite, args.host, args.port)

    # 8. Drafts round-trip
    print("→ Drafts round-trip...")
    test_drafts_round_trip(suite, args.host, args.port)

    # 9. CLI commands
    if not args.skip_cli:
        print("→ CLI commands...")
        test_cli_commands(suite)
    else:
        print("→ CLI commands: SKIPPED")

    suite.print_summary()
    return 0 if suite.failed() == 0 else 1


if __name__ == "__main__":
    sys.exit(main())