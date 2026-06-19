"""Cookie + login management for the web UI.

Provides:
  GET  /api/cookies              — list all cookies (li_at masked)
  GET  /api/cookies/health       — test if li_at works against LinkedIn
  POST /api/cookies/li_at        — set new li_at value (writes file + env)
  POST /api/cookies/import       — import full cookie JSON (paste from DevTools)
  POST /api/login/start          — launch headed Playwright login on server
  GET  /api/login/status         — is login running? last result?
  POST /api/login/cancel         — cancel in-progress login
  GET  /api/profile/health       — combined: cookie age + LinkedIn reachability

Cookie + login file paths (env-overridable):
  LI_AT_FILE    default: $XDG_DATA_HOME/linkedin-mcp-pro/li_at (falls back to ~/.local/share/linkedin-mcp-pro/li_at)
  PROFILE_DIR   default: ~/.linkedin-mcp/profile
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import load_config

log = logging.getLogger("linkedin_mcp.cookies_api")

router = APIRouter()


def _user_data_dir() -> Path:
    """Resolve user-writable data dir (XDG-aware, never /etc).

    Used as fallback when LI_AT_FILE / PROFILE_DIR env overrides point to
    non-writable paths (e.g. the legacy /etc/... default from older builds).
    """
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "linkedin-mcp-pro"

# ---------------------------------------------------------------------------
# In-memory login state (single-process; fine for typical usage)
# ---------------------------------------------------------------------------

_login_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "ok": None,
    "error": None,
    "browser_pid": None,
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class LiAtPayload(BaseModel):
    value: str
    note: str = "set via web UI"


class CookieImport(BaseModel):
    cookies: list[dict[str, Any]]  # standard Playwright cookie shape


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _li_at_path() -> Path:
    """Resolve li_at file path.

    Priority:
      1. $LI_AT_FILE env (if set and writable / parent creatable)
      2. $XDG_DATA_HOME/linkedin-mcp-pro/li_at
      3. ~/.local/share/linkedin-mcp-pro/li_at

    Never defaults to /etc/... — those paths are reserved for system packages
    and would fail with PermissionError for non-root users.
    """
    explicit = os.environ.get("LI_AT_FILE", "").strip()
    if explicit:
        return Path(explicit)
    return _user_data_dir() / "li_at"


def _profile_dir() -> Path:
    p = os.environ.get("LINKEDIN_MCP_PROFILE_DIR", "").strip()
    if p:
        return Path(p)
    return Path.home() / ".linkedin-mcp" / "profile"


def _storage_state_path() -> Path:
    return _profile_dir() / "storage_state.json"


def _mask(value: str, head: int = 8, tail: int = 6) -> str:
    """Mask secret for display — keep head + tail, replace middle with •."""
    if not value or len(value) <= head + tail + 3:
        return "•" * len(value) if value else "(empty)"
    return value[:head] + "•" * min(20, len(value) - head - tail) + value[-tail:]


def _read_li_at() -> Optional[str]:
    p = _li_at_path()
    if not p.exists():
        return None
    try:
        return p.read_text().strip() or None
    except Exception as e:
        log.warning("read li_at failed: %s", e)
        return None


def _write_li_at(value: str, note: str = "") -> dict[str, Any]:
    """Write li_at to disk (0600 perms) + log audit row.

    Falls back transparently to $XDG_DATA_HOME/linkedin-mcp-pro/li_at if the
    configured LI_AT_FILE path is not writable (e.g. legacy /etc/... default).
    Raises HTTPException(500) with a safe message (no secret leakage) on
    unrecoverable failure.
    """
    requested = _li_at_path()
    fallback_used = False

    # Try requested path first
    try:
        requested.parent.mkdir(parents=True, exist_ok=True)
        test = requested.parent / ".write_test_linkedin_mcp"
        test.touch()
        test.unlink()
        target = requested
    except (PermissionError, OSError) as e:
        # Fall back to user-writable data dir
        target = _user_data_dir() / "li_at"
        fallback_used = True
        log.warning(
            "li_at write to %s failed (%s); falling back to %s",
            requested, e, target,
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e2:
            log.error("li_at fallback mkdir also failed: %s", e2)
            raise HTTPException(
                status_code=500,
                detail=f"Cannot write li_at file: tried {requested} and {target}",
            )

    try:
        target.write_text(value.strip())
        try:
            os.chmod(target, 0o600)
        except Exception:
            pass
    except Exception as e:
        log.error("li_at write failed at %s: %s", target, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write li_at to {target}: {type(e).__name__}",
        )

    # Also update env for this process + audit
    os.environ["LI_AT"] = value.strip()
    log.info("li_at updated via web UI (note=%r, path=%s)", note, target)
    return {
        "path": str(target),
        "fallback_used": fallback_used,
        "requested_path": str(requested),
        "bytes": target.stat().st_size,
        "mode": oct(target.stat().st_mode & 0o777),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _read_storage_state() -> dict[str, Any]:
    p = _storage_state_path()
    if not p.exists():
        return {"cookies": [], "origins": []}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log.warning("storage_state unreadable: %s", e)
        return {"cookies": [], "origins": [], "error": str(e)}


def _check_li_at_validity(li_at: str, timeout: float = 8.0) -> dict[str, Any]:
    """HEAD request to LinkedIn with the cookie. Returns health dict.

    LinkedIn returns 302 if cookie alone is invalid (anti-bot). It returns
    200 with full HTML + 'csrfToken' marker if logged in. We use full cookie
    jar from storage_state.json when available for accurate detection.
    """
    if not li_at:
        return {"ok": False, "error": "li_at is empty", "status_code": None}

    # Prefer full cookie jar if available (LinkedIn needs JSESSIONID + li_at)
    cookie_header = f"li_at={li_at}"
    storage_state = _read_storage_state()
    for c in storage_state.get("cookies", []):
        if "linkedin" in c.get("domain", "") and c.get("name") != "li_at":
            cookie_header += f"; {c['name']}={c['value']}"

    url = "https://www.linkedin.com/feed/"
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Cookie": cookie_header,
        "Accept": "text/html",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            elapsed_ms = int((time.time() - t0) * 1000)
            body = r.read(16384)
            html = body.decode("utf-8", "replace")
            # Login detection: title-based + csrfToken
            # Title "Feed | LinkedIn" = logged in
            # Title "Sign In | LinkedIn" or redirect to /login = NOT logged in
            import re
            title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else ""
            title_lower = title.lower()
            is_logged_in = (
                title_lower.startswith("feed | linkedin")
                or title_lower.startswith("home | linkedin")
                or "feed | linkedin" in title_lower
                or ("csrfToken" in html and "feed/index" in r.url)
            )
            return {
                "ok": is_logged_in,
                "http_status": r.status,
                "final_url": r.url,
                "title": title,
                "elapsed_ms": elapsed_ms,
                "is_logged_in": is_logged_in,
                "body_size_indicator": len(html),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "http_status": e.code,
            "error": f"HTTP {e.code}",
            "elapsed_ms": int((time.time() - t0) * 1000),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {
            "ok": False,
            "http_status": None,
            "error": str(e)[:200],
            "elapsed_ms": int((time.time() - t0) * 1000),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Cookie endpoints
# ---------------------------------------------------------------------------


@router.get("/api/cookies")
def api_cookies() -> dict[str, Any]:
    """List all cookies + show masked li_at + file metadata."""
    state = _read_storage_state()
    cookies = state.get("cookies", [])
    li_at = _read_li_at()

    # Identify li_at inside cookie list (mask its value)
    masked_cookies = []
    for c in cookies:
        cc = dict(c)
        if cc.get("name") == "li_at":
            cc["value_masked"] = _mask(cc.get("value", ""))
            cc["value"] = cc["value_masked"]  # never return raw
            cc["_is_auth"] = True
        masked_cookies.append(cc)

    # Cross-check: li_at file vs cookies
    li_at_in_cookies = any(c.get("name") == "li_at" for c in cookies)
    consistency = "match" if (li_at and li_at_in_cookies) or (not li_at and not li_at_in_cookies) else "mismatch"

    return {
        "count": len(cookies),
        "domains": sorted({c.get("domain", "") for c in cookies}),
        "li_at_file": {
            "path": str(_li_at_path()),
            "exists": _li_at_path().exists(),
            "bytes": _li_at_path().stat().st_size if _li_at_path().exists() else 0,
            "mode": oct(_li_at_path().stat().st_mode & 0o777) if _li_at_path().exists() else None,
            "value_masked": _mask(li_at) if li_at else "(not set)",
        },
        "storage_state": {
            "path": str(_storage_state_path()),
            "exists": _storage_state_path().exists(),
            "bytes": _storage_state_path().stat().st_size if _storage_state_path().exists() else 0,
        },
        "consistency": consistency,
        "cookies": masked_cookies,
    }


@router.get("/api/cookies/health")
def api_cookies_health() -> dict[str, Any]:
    """Check if current li_at cookie works against LinkedIn."""
    li_at = _read_li_at()
    if not li_at:
        raise HTTPException(status_code=404, detail="li_at not configured")
    return _check_li_at_validity(li_at)


@router.post("/api/cookies/li_at")
def api_cookies_set_li_at(p: LiAtPayload) -> dict[str, Any]:
    """Replace li_at value. Writes file (0600), updates env, returns health check.

    On filesystem permission errors, _write_li_at transparently falls back to
    $XDG_DATA_HOME/linkedin-mcp-pro/li_at (per-user, never /etc/...).
    """
    if not p.value or len(p.value) < 50:
        raise HTTPException(status_code=400, detail="li_at too short (min 50 chars)")
    if len(p.value) > 500:
        raise HTTPException(status_code=400, detail="li_at too long (max 500 chars)")
    meta = _write_li_at(p.value, note=p.note)
    health = _check_li_at_validity(p.value)
    return {"wrote": meta, "health": health}


@router.post("/api/cookies/import")
def api_cookies_import(body: CookieImport) -> dict[str, Any]:
    """Replace storage_state.json with pasted cookie array.

    Accepts either:
      - [{name, value, domain, path, expires, httpOnly, secure, sameSite}, ...]
      - Export from EditThisCookie / Cookie-Editor browser extensions
    """
    if not body.cookies:
        raise HTTPException(status_code=400, detail="cookies array is empty")

    # Normalize to Playwright cookie shape
    norm = []
    for c in body.cookies:
        if "name" not in c or "value" not in c:
            continue
        domain = c.get("domain") or c.get("Domain") or ".linkedin.com"
        if not domain.startswith(".") and "linkedin" not in domain:
            raise HTTPException(
                status_code=400,
                detail=f"non-LinkedIn domain rejected: {domain!r}",
            )
        norm.append({
            "name": c["name"],
            "value": c["value"],
            "domain": domain,
            "path": c.get("path") or c.get("Path") or "/",
            "expires": c.get("expires") or c.get("ExpirationDate") or -1,
            "httpOnly": c.get("httpOnly", True),
            "secure": c.get("secure", True),
            "sameSite": c.get("sameSite") or c.get("SameSite") or "None",
        })

    p = _storage_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"cookies": norm, "origins": []}, indent=2))
    os.chmod(p, 0o600)

    # If li_at was included, also write the standalone file
    li_at = next((c["value"] for c in norm if c["name"] == "li_at"), None)
    if li_at:
        _write_li_at(li_at, note="set via cookie import")

    return {
        "imported": len(norm),
        "storage_state_bytes": p.stat().st_size,
        "li_at_synced": bool(li_at),
        "health": _check_li_at_validity(li_at) if li_at else None,
    }


# ---------------------------------------------------------------------------
# Login endpoints (Playwright headed)
# ---------------------------------------------------------------------------


@router.get("/api/login/status")
def api_login_status() -> dict[str, Any]:
    """Current login process status."""
    out = dict(_login_state)
    # Augment with browser process liveness if PID known
    pid = out.get("browser_pid")
    if pid:
        try:
            os.kill(pid, 0)
            out["browser_alive"] = True
        except (OSError, ProcessLookupError):
            out["browser_alive"] = False
    return out


@router.post("/api/login/start")
def api_login_start(headless: bool = False) -> dict[str, Any]:
    """Launch Playwright login in background.

    Args:
      headless: if True, runs without visible browser (useful for CI).
                If False, opens Chromium on the server machine — user must
                have VNC/RDP/physical access, OR run linkedin-mcp-login
                from CLI instead.
    """
    if _login_state["running"]:
        raise HTTPException(status_code=409, detail="login already running")
    _login_state.update({
        "running": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "ok": None,
        "error": None,
        "browser_pid": None,
    })
    try:
        # Launch as detached subprocess
        env = os.environ.copy()
        env.setdefault("PYTHONPATH", "/home/admin/linkedin-mcp-pro")
        proc = subprocess.Popen(
            ["linkedin-mcp-login", "--headless" if headless else "--headed"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _login_state["browser_pid"] = proc.pid
        return {
            "ok": True,
            "pid": proc.pid,
            "headless": headless,
            "message": "Login started. Watch for completion via /api/login/status.",
            "tip": "If headless=False, view the browser via VNC/RDP. Or run linkedin-mcp-login from CLI on your laptop.",
        }
    except Exception as e:
        _login_state.update({
            "running": False,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": str(e),
        })
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/login/cancel")
def api_login_cancel() -> dict[str, Any]:
    pid = _login_state.get("browser_pid")
    if pid and _login_state["running"]:
        try:
            os.killpg(os.getpgid(pid), 15)
        except Exception:
            pass
    _login_state.update({
        "running": False,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "ok": False,
        "error": "cancelled",
    })
    return {"cancelled": True}


@router.get("/api/profile/health")
def api_profile_health() -> dict[str, Any]:
    """Combined: cookie presence + freshness + LinkedIn reachability."""
    li_at = _read_li_at()
    state = _read_storage_state()
    storage_age = None
    storage_mtime = None
    if _storage_state_path().exists():
        mtime = _storage_state_path().stat().st_mtime
        storage_mtime = mtime
        storage_age_days = (time.time() - mtime) / 86400
        storage_age = round(storage_age_days, 2)

    health = _check_li_at_validity(li_at) if li_at else {"ok": False, "error": "no cookie"}

    # Timeline: relative-time strings for the UI
    def _ago(ts: float) -> str:
        s = int(time.time() - ts)
        if s < 60: return f"{s}s ago"
        if s < 3600: return f"{s // 60}m ago"
        if s < 86400: return f"{s // 3600}h ago"
        return f"{s // 86400}d ago"

    # Pull last audit events for "last verified" / "last failed"
    timeline: dict = {"last_saved": None, "last_verified": None, "last_failed": None}
    try:
        # Find the actual audit db — linkedin-mcp stores it under data/
        candidates = [
            _li_at_path().parent / "audit.db",
            Path("/home/admin/linkedin-mcp-pro/data/linkedin-mcp-pro.db"),
        ]
        # Also try discover via env / config
        try:
            from .config import load_config
            cfg = load_config()
            dbp = getattr(cfg, "db_path", None)
            if dbp:
                candidates.insert(0, Path(dbp))
        except Exception:
            pass
        seen = set()
        for db_path in candidates:
            if db_path in seen or not db_path.exists():
                continue
            seen.add(db_path)
            import sqlite3
            with sqlite3.connect(str(db_path)) as conn:
                # Detect column name (created_at vs ts)
                cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()]
                if not cols:
                    continue
                ts_col = "created_at" if "created_at" in cols else ("ts" if "ts" in cols else cols[0])
                rows = conn.execute(
                    f"SELECT {ts_col}, status, action, detail FROM audit_log "
                    "WHERE action IN ('login_verify','cookie_set','cookie_import','login_start','profile_load') "
                    f"ORDER BY {ts_col} DESC LIMIT 30"
                ).fetchall()
                for ts, status_v, action, detail in rows:
                    if not ts:
                        continue
                    try:
                        ts_f = float(ts) if isinstance(ts, (int, float)) else None
                        if ts_f is None:
                            from datetime import datetime as _dt
                            # Try parse ISO string
                            try:
                                ts_f = _dt.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                            except Exception:
                                continue
                    except Exception:
                        continue
                    rel = _ago(ts_f)
                    if action in ("cookie_set", "cookie_import") and timeline["last_saved"] is None:
                        timeline["last_saved"] = rel
                    if action in ("login_verify", "profile_load"):
                        ok = str(status_v).lower() in ("ok", "success", "verified", "200")
                        if ok and timeline["last_verified"] is None:
                            timeline["last_verified"] = rel
                        if not ok and timeline["last_failed"] is None:
                            timeline["last_failed"] = rel
                break  # only first matching db
    except Exception:
        pass
    if timeline["last_saved"] is None and storage_mtime:
        timeline["last_saved"] = _ago(storage_mtime)

    return {
        "li_at_present": bool(li_at),
        "li_at_file": str(_li_at_path()),
        "cookie_count": len(state.get("cookies", [])),
        "storage_state_age_days": storage_age,
        "linkedin_reachable": health.get("http_status") is not None,
        "linkedin_logged_in": health.get("is_logged_in", False),
        "linkedin_http_status": health.get("http_status"),
        "linkedin_elapsed_ms": health.get("elapsed_ms"),
        "last_error": health.get("error") or health.get("title") or None,
        "overall_ok": bool(li_at) and health.get("is_logged_in", False),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "timeline": timeline,
    }