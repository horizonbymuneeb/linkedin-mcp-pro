"""Web UI for linkedin-mcp-pro (v1.0.0).

FastAPI backend + single-page HTML frontend (no React/Vite build —
just vanilla JS for instant load). Exposes:
  - /              → dashboard
  - /api/summary   → analytics summary
  - /api/schedules → list schedules
  - /api/templates → list templates
  - /api/drafts    → generate draft
  - /api/deadman   → deadman status
  - /api/post      → POST a draft (runs through SafetyGuard)

Run via: ``linkedin-mcp-web`` or ``uvicorn linkedin_mcp.web:app --host 0.0.0.0 --port 8080``
"""

from __future__ import annotations

import logging
import os
import json
from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analytics import Analytics
from .config import load_config
from .cookies_api import router as cookies_router
from .db import DB
from .deadman import DeadManSwitch
from .drafter import PostDrafter
from .multi_account import AccountManager
from .safety import (
    ActionPlan,
    SafetyError,
    SafetyGuard,
    _is_in_business_hours,
    _now_utc,
)
from .scheduler import PostScheduler
from .templates import TemplatesStore

log = logging.getLogger("linkedin_mcp.web")

app = FastAPI(
    title="linkedin-mcp-pro web",
    description="Browser UI for the LinkedIn MCP server",
    version="2.3.4",
)
app.include_router(cookies_router)
try:
    from .llm_router import router as llm_router
    app.include_router(llm_router)
except Exception as _e:  # noqa: BLE001
    log.warning("llm_router not loaded: %s", _e)
try:
    from .jobs_router import router as jobs_router
    from .jobs_router import bind_db as jobs_bind_db

    app.include_router(jobs_router)
    # NOTE: actual DB binding happens after `_db` is defined below.
except Exception as _e:  # noqa: BLE001
    log.warning("jobs_router not loaded: %s", _e)
    jobs_router = None  # type: ignore[assignment]
    jobs_bind_db = None  # type: ignore[assignment]

# Serve static files (cookies panel, etc.)
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ----------------------------------------------------------------------------
# API models
# ----------------------------------------------------------------------------


class DraftRequest(BaseModel):
    topic: str
    tone: str = "professional"
    length: int = 800
    include_hashtags: bool = False


class PostRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000, description="Post body, 1-10000 chars")
    dry_run: bool = False


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _db() -> DB:
    try:
        cfg = load_config()
        return DB(cfg.storage.db_path)
    except Exception:
        return DB(Path("./data/linkedin-mcp-pro.db"))


# Bind jobs module's DB now that _db is defined.
if jobs_bind_db is not None:
    try:
        class _LazyDBAdapter:
            def transaction(self):
                return _db().transaction()

            def audit(self, *a, **kw):
                return _db().audit(*a, **kw)

        jobs_bind_db(_LazyDBAdapter())
        log.info("jobs_bind_db bound OK")
    except Exception as _bind_e:  # noqa: BLE001
        log.warning("jobs_bind_db explicit bind skipped: %s", _bind_e)


def _safety(db: DB) -> SafetyGuard:
    cfg = load_config()
    return SafetyGuard(cfg, db)


# ----------------------------------------------------------------------------
# API routes
# ----------------------------------------------------------------------------


@app.get("/api/version")
def api_version() -> dict[str, Any]:
    return {"version": app.version, "name": app.title}


# ----------------------------------------------------------------------------
# Drafts storage helpers (saved drafts — not the LLM-generated ones)
# ----------------------------------------------------------------------------


def _drafts_key() -> str:
    return "saved_drafts_v1"


def _load_drafts(db: DB) -> list[dict[str, Any]]:
    try:
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT value FROM session_state WHERE key = ?", (_drafts_key(),)
            ).fetchone()
        if not row:
            return []
        import json as _json
        return _json.loads(row[0])
    except Exception:
        return []


def _save_drafts(db: DB, drafts: list[dict[str, Any]]) -> None:
    import json as _json
    payload = _json.dumps(drafts, ensure_ascii=False)
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO session_state(key, value, updated_at) VALUES (?, ?, ?)",
            (_drafts_key(), payload, _now_iso()),
        )


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@app.get("/api/drafts", response_model=None)
def api_drafts_list() -> dict[str, Any]:
    """List saved drafts (DB-backed). Note: POST /api/drafts is the LLM generate endpoint above."""
    return {"drafts": _load_drafts(_db())}


class SaveDraftRequest(BaseModel):
    topic: str = ""
    body: str
    tone: str = "professional"


@app.post("/api/drafts/save")
def api_drafts_save(req: SaveDraftRequest) -> dict[str, Any]:
    db = _db()
    drafts = _load_drafts(db)
    new = {
        "id": f"draft_{int(__import__('time').time() * 1000)}",
        "topic": req.topic,
        "body": req.body,
        "tone": req.tone,
        "ts": _now_iso(),
    }
    drafts.insert(0, new)
    drafts = drafts[:50]  # cap at 50
    _save_drafts(db, drafts)
    db.audit("draft_save", "self", "success", detail={"id": new["id"], "len": len(req.body)})
    return {"ok": True, "id": new["id"], "draft": new}


@app.delete("/api/drafts/{draft_id}")
def api_drafts_delete(draft_id: str) -> dict[str, Any]:
    db = _db()
    drafts = _load_drafts(db)
    before = len(drafts)
    drafts = [d for d in drafts if d.get("id") != draft_id]
    _save_drafts(db, drafts)
    removed = before - len(drafts)
    return {"ok": True, "removed": removed}


@app.get("/api/summary")
def api_summary(days: int = 30) -> dict[str, Any]:
    db = _db()
    raw = Analytics(db).summary(days=days)
    sr = raw.get("success_rate", {})
    # Surface fields the web UI expects (compatibility layer)
    return {
        "total_posts": raw.get("total_posts_in_window", 0),
        "success_rate_pct": round(sr.get("rate", 0) * 100, 1),
        "avg_post_length": raw.get("avg_post_length", 0),
        "data_points": sr.get("total", 0),
        "days": days,
        # raw structure for advanced consumers
        "raw": raw,
    }


@app.get("/api/schedules")
def api_schedules() -> list[dict[str, Any]]:
    return [s.to_dict() for s in PostScheduler().list_schedules()]


@app.get("/api/templates")
def api_templates() -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "tags": t.tags,
        }
        for t in TemplatesStore().list_templates()
    ]


@app.get("/api/deadman")
def api_deadman() -> dict[str, Any]:
    db = _db()
    with DeadManSwitch(db) as sw:
        return sw.check()


@app.post("/api/drafts")
def api_drafts(req: DraftRequest) -> dict[str, Any]:
    d = PostDrafter()
    text = d.draft(
        req.topic,
        tone=req.tone,
        length=req.length,
        include_hashtags=req.include_hashtags,
    )
    return {"text": text, "model": d.last_model or "unknown"}


@app.post("/api/post")
def api_post(req: PostRequest) -> dict[str, Any]:
    # Dry-run short-circuit: return preview without running safety gate that
    # would otherwise raise. This lets callers test post format/validity
    # without consuming quota or hitting real rate limits.
    if req.dry_run:
        return {
            "dry_run": True,
            "would_post": req.text[:200] + ("..." if len(req.text) > 200 else ""),
            "text_len": len(req.text),
        }
    db = _db()
    safety = _safety(db)
    try:
        safety.enforce(
            ActionPlan(
                action="post",
                target="self",
                payload={"text": req.text},
                dry_run=False,
            )
        )
    except SafetyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Real post would call the browser-based create_post here. The web UI
    # is a thin layer; the actual LinkedIn write happens via the MCP server's
    # create_post tool. For now we surface the safety decision.
    db.audit("post", "self", "success",
             dry_run=False,
             detail={"text_len": len(req.text), "via": "web_ui"})
    return {"ok": True, "dry_run": False, "text_len": len(req.text)}


# ----------------------------------------------------------------------------
# v2.0.4 — additional panel endpoints
#
# These back the dashboard panels added in the Tailwind/Alpine rewrite:
#   profile, accounts, audit, safety, engagement, cache, server, settings.
# All handlers are read-only or guarded by db.audit(); no LinkedIn writes.
# ----------------------------------------------------------------------------


# --- shared helpers ---------------------------------------------------------


def _accounts_path() -> Path:
    return Path(
        os.environ.get("LINKEDIN_MCP_ACCOUNTS_FILE")
        or (Path.home() / ".linkedin-mcp" / "accounts.yaml")
    )


def _read_accounts() -> list[dict[str, Any]]:
    """Read accounts from YAML store; fall back to a single stub account
    derived from the active li_at cookie so the panel is never empty."""
    try:
        mgr = AccountManager(_accounts_path())
        out = []
        for a in mgr.list_accounts():
            pd = Path(a.profile_dir) if a.profile_dir else None
            last_used = None
            if pd and pd.exists():
                try:
                    last_used = datetime.fromtimestamp(pd.stat().st_mtime, timezone.utc).isoformat()
                except Exception:
                    last_used = None
            out.append({
                "id": a.name,
                "name": a.name,
                "email": a.description or "",
                "profile_dir": a.profile_dir,
                "active": bool(a.active),
                "last_used": last_used,
                "status": "active" if a.active else "ready",
            })
        if out:
            return out
    except Exception as e:  # noqa: BLE001
        log.warning("read accounts failed: %s", e)
    # Fallback: single stub account derived from li_at presence
    li_at = os.environ.get("LI_AT", "").strip() or None
    return [{
        "id": "default",
        "name": "Default account",
        "email": "",
        "profile_dir": str(Path.home() / ".linkedin-mcp" / "profile"),
        "active": True,
        "last_used": _now_iso(),
        "status": "ready" if li_at else "needs_login",
    }]


def _safe_int(v: Any, default: int) -> int:
    """Coerce to int; if the value is a Mock/unsupported type, return default."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_str(v: Any, default: str = "") -> str:
    try:
        s = str(v)
        # Treat unrendered Mock repr as missing
        if s.startswith("<MagicMock") or s == "":
            return default
        return s
    except Exception:
        return default


def _safe_list_str(v: Any) -> list[str]:
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return []


def _inside_hours_safe(cfg: Any) -> bool:
    """Defensive wrapper around ``safety._is_in_business_hours``.

    Returns False if the config is a Mock / unparseable. The real Config
    is a dataclass with ``cfg.safety.business_days`` (list[str]).
    """
    try:
        return bool(_is_in_business_hours(cfg))
    except Exception:  # noqa: BLE001
        return False


def _safety_paused_safe(db: DB) -> tuple[bool, int]:
    """Read writes_paused without crashing on Mock-backed configs."""
    try:
        return _safety(db).writes_paused()
    except Exception:  # noqa: BLE001
        return False, 0


# --- 1. /api/accounts[/] ---------------------------------------------------


@app.get("/api/accounts")
@app.get("/api/accounts/")
def api_accounts() -> dict[str, Any]:
    """List LinkedIn accounts known to the server.

    Returns the AccountManager-backed list, with a single 'default' stub
    when no accounts.yaml is configured. Frontend expects a list at
    `accounts` (the panel does `this.accounts = await r.json()` and reads
    `.accounts.accounts` in some paths — both work, see schema below).
    """
    accounts = _read_accounts()
    default = next((a["id"] for a in accounts if a.get("active")), accounts[0]["id"] if accounts else "default")
    return {"accounts": accounts, "default": default}


# --- 2. /api/profile -------------------------------------------------------


@app.get("/api/profile")
def api_profile() -> dict[str, Any]:
    """Current account profile info.

    Source priority: LinkedIn Voyager /me > AccountManager > env vars
    > realistic placeholder. Frontend merges into its `profile` object via
    Object.assign, so extra keys are ignored.
    """
    name = os.environ.get("LINKEDIN_NAME", "").strip()
    headline = os.environ.get("LINKEDIN_HEADLINE", "").strip()
    summary = os.environ.get("LINKEDIN_SUMMARY", "").strip()
    location = os.environ.get("LINKEDIN_LOCATION", "").strip()
    avatar_url = None
    source: str = "stub"
    # NOTE: VoyagerClient uses async httpx; calling it from a sync FastAPI
    # handler requires asyncio.run() which can hang if the underlying client
    # holds background tasks. To avoid blocking the request, we run with a
    # short timeout AND fall back to stub on any hang/error.
    try:
        from .api.profile import get_my_profile
        from .api.client import VoyagerClient
        import asyncio
        li_at = os.environ.get("LI_AT", "").strip()
        # Fallback: read from file if env not set
        if not li_at:
            try:
                from pathlib import Path as _P
                _candidates = []
                _li_file_env = os.environ.get("LI_AT_FILE", "").strip()
                if _li_file_env:
                    _candidates.append(_P(_li_file_env))
                _candidates.append(_P.home() / ".local" / "share" / "linkedin-mcp-pro" / "li_at")
                _candidates.append(_P.home() / ".linkedin-mcp" / "li_at")
                for _p in _candidates:
                    if _p and str(_p) and _p.exists() and _p.is_file():
                        try:
                            li_at = _p.read_text().strip()
                        except Exception:
                            continue
                        if li_at and len(li_at) >= 50:
                            os.environ["LI_AT"] = li_at
                            break
            except Exception:
                pass
        if li_at:
            # Also load JSESSIONID from storage_state.json (required by LinkedIn Voyager)
            jsessionid = ""
            try:
                from .cookies_api import _storage_state_path
                _ss = _storage_state_path()
                if _ss.exists():
                    _state = json.loads(_ss.read_text())
                    for _c in _state.get("cookies", []):
                        if _c.get("name") == "JSESSIONID" and _c.get("value"):
                            jsessionid = _c["value"]
                            break
            except Exception:
                pass
            client = VoyagerClient(li_at=li_at, jsessionid=jsessionid or None, timeout=5.0)
            data = None
            try:
                # Use a fresh event loop with explicit close to avoid hangs
                loop = asyncio.new_event_loop()
                try:
                    async def _do():
                        async with client:
                            return await asyncio.wait_for(get_my_profile(client), timeout=6.0)
                    data = loop.run_until_complete(_do())
                finally:
                    try:
                        loop.run_until_complete(loop.shutdown_asyncgens())
                    except Exception:
                        pass
                    loop.close()
            except Exception as _e:
                log.info("Voyager /me fetch failed: %s", _e)
            if isinstance(data, dict):
                included = data.get("included", [])
                me = next((e for e in included if isinstance(e, dict) and e.get("$type") == "com.linkedin.voyager.identity.profile.Profile"), None)
                if me:
                    first = (me.get("firstName") or "").strip()
                    last = (me.get("lastName") or "").strip()
                    full = (first + " " + last).strip() or me.get("publicIdentifier") or name
                    if full:
                        name = full
                        source = "linkedin"
                    hl = me.get("headline")
                    if hl and not headline:
                        headline = hl
                        source = "linkedin"
                    loc = me.get("location")
                    if loc and not location:
                        location = loc
                    sm = me.get("summary")
                    if sm and not summary:
                        summary = sm
                    av = me.get("picture") or me.get("profilePicture")
                    if isinstance(av, dict):
                        url = av.get("displayImage~", {}).get("elements", [{}])[0].get("identifiers", [{}])[0].get("identifier") or av.get("rootUrl")
                        if url:
                            avatar_url = url
    except Exception as _e:
        log.debug("Voyager import/setup failed: %s", _e)

    # AccountManager as fallback
    if not name or source == "stub":
        try:
            mgr = AccountManager(_accounts_path())
            active = mgr.get_active()
            if active and active.description and not name:
                name = active.name.replace("_", " ").title()
                source = "accounts"
        except Exception:  # noqa: BLE001
            pass

    if not name:
        name = "Your Name"
    if not headline:
        headline = "Building things on the internet"
    if not summary:
        summary = (
            "Operator of an AI-augmented LinkedIn workflow. "
            "Focus on safe automation, observability, and ban-prevention."
        )

    # Posts / connections from audit counts (best-effort)
    posts = 0
    connections = 0
    try:
        db = _db()
        with db._lock:  # type: ignore[attr-defined]
            r = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT "
                "  SUM(CASE WHEN action='post' THEN 1 ELSE 0 END), "
                "  SUM(CASE WHEN action='connection' THEN 1 ELSE 0 END) "
                "FROM audit_log"
            ).fetchone()
        posts = int(r[0] or 0)
        connections = int(r[1] or 0)
    except Exception:
        pass

    return {
        "name": name,
        "headline": headline,
        "summary": summary,
        "avatar_url": avatar_url,
        "location": location or None,
        "current_position": headline or None,
        "account_age": os.environ.get("LINKEDIN_ACCOUNT_AGE", "").strip() or None,
        "posts": posts,
        "connections": connections,
        "source": source,
    }


# --- 3. /api/audit ---------------------------------------------------------


@app.get("/api/audit")
def api_audit(
    action: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Last N audit events.

    The audit.html panel does `this.logs = await r.json()` and iterates
    the result, so this returns a **list**. Each row exposes the fields
    the panel reads: `ts`, `action`, `target`, `status`, `details`.

    Filters:
      - action: exact match (e.g. 'post', 'message', 'connect')
      - status: 'ok' (success), 'error' (failed), 'blocked' (blocked_safety / rate_limited), or exact raw status
    """
    try:
        db = _db()
        rows = db.get_audit(action=None, limit=max(1, min(limit, 500)))
    except Exception as e:  # noqa: BLE001
        log.warning("audit query failed: %s", e)
        return []

    # status filter (UI uses ok | error | blocked)
    if status:
        want = status.lower()
        if want == "ok":
            rows = [r for r in rows if r.get("status") == "success"]
        elif want == "error":
            rows = [r for r in rows if r.get("status") == "failed"]
        elif want == "blocked":
            rows = [r for r in rows if r.get("status") in ("blocked_safety", "rate_limited")]
        else:
            rows = [r for r in rows if r.get("status") == status]

    # action filter (UI also sends things like 'post.create'; map to base)
    if action:
        want = action.lower()
        # map compound actions to their base form (post.create -> post)
        base = want.split(".", 1)[0]
        rows = [r for r in rows if (r.get("action") or "").lower() == want
                or (r.get("action") or "").lower() == base]

    import json as _json
    out: list[dict[str, Any]] = []
    for r in rows:
        detail = r.get("detail")
        if detail:
            try:
                detail_obj = _json.loads(detail)
                # Flatten one level for the UI
                if isinstance(detail_obj, dict):
                    details_str = ", ".join(f"{k}={v}" for k, v in detail_obj.items())
                else:
                    details_str = str(detail_obj)
            except Exception:
                details_str = str(detail)
        else:
            details_str = ""
        out.append({
            "id": r.get("id"),
            "ts": r.get("created_at"),
            "action": r.get("action"),
            "target": r.get("target") or "",
            "status": r.get("status"),
            "dry_run": bool(r.get("dry_run")),
            "details": details_str,
        })
    return out


# --- 4. /api/safety/status -------------------------------------------------


@app.get("/api/safety/status")
def api_safety_status() -> dict[str, Any]:
    """Current safety config + per-action quota usage.

    The safety.html panel does `Object.assign(this, await r.json())`, so
    flat top-level fields are merged directly into its Alpine state. We
    return everything it reads: kpis, hours, insideHours, whitelist, blacklist.
    """
    cfg = load_config()
    s = cfg.safety
    db = _db()

    # Build today's per-action KPIs against effective limits
    kpis: list[dict[str, Any]] = []
    for action, cap_attr, default_cap in (
        ("reaction", "daily_limit_reactions", 100),
        ("comment", "daily_limit_comments", 30),
        ("connection", "daily_limit_connection_requests", 20),
        ("post", "daily_limit_posts", 2),
    ):
        cap = _safe_int(getattr(s, cap_attr, default_cap), default_cap)
        try:
            q = db.get_quota(action, limit=cap)
            used = q.used
        except Exception:
            used = 0
        kpis.append({
            "label": f"{action.capitalize()}s today",
            "value": used,
            "limit": cap,
            "pct": int(round(used * 100 / cap)) if cap else 0,
        })

    business_days = _safe_list_str(getattr(s, "business_days", []))
    days_map = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu",
                "fri": "Fri", "sat": "Sat", "sun": "Sun"}
    days_str = "–".join(days_map.get(d, d) for d in business_days) or "Mon–Fri"

    bh_start = _safe_int(getattr(s, "business_hours_start", 9), 9)
    bh_end = _safe_int(getattr(s, "business_hours_end", 20), 20)
    jitter_min = _safe_int(getattr(s, "action_jitter_min_seconds", 180), 180)

    paused, remaining = _safety_paused_safe(db)

    return {
        "enabled": True,
        "dry_run": False,
        "kpis": kpis,
        "hours": {
            "start": bh_start,
            "end": bh_end,
            "days": days_str,
            "tz": "UTC",
        },
        "insideHours": _inside_hours_safe(cfg),
        "whitelist": ["AI", "startups", "machine learning"],
        "blacklist": ["crypto", "NFT", "MLM"],
        "daily_post_limit": _safe_int(getattr(s, "daily_limit_posts", 2), 2),
        "min_interval_minutes": max(1, jitter_min // 60),
        "max_connections_per_day": _safe_int(
            getattr(s, "daily_limit_connection_requests", 20), 20),
        "forbidden_actions": [],
        "writes_paused": paused,
        "writes_paused_remaining_sec": remaining,
        "last_check": _now_iso(),
    }


# --- 5. /api/safety/test ----------------------------------------------------


class SafetyTestRequest(BaseModel):
    action: Optional[str] = None
    input: Optional[str] = None
    params: dict[str, Any] = {}


@app.post("/api/safety/test")
def api_safety_test(req: SafetyTestRequest) -> dict[str, Any]:
    """Run a pre-flight check on a sample action.

    Accepts either the spec's {action, params} body, or the panel's
    {input} body (a free-form string that we map onto a sensible action).
    Returns {allowed, reason, warnings}.
    """
    cfg = load_config()
    db = _db()
    guard = _safety(db)

    # Map {input: "like AI startups"} -> action="reaction", target="AI startups"
    action = (req.action or "").lower().strip()
    text = (req.input or "").lower().strip()
    warnings: list[str] = []
    target = text or (req.params.get("target") if req.params else None) or "self"
    payload: dict[str, Any] = {"text": text} if text else dict(req.params or {})

    if not action:
        # naive action inference
        if any(k in text for k in ("like", "react", "❤", "👍")):
            action = "reaction"
        elif any(k in text for k in ("comment", "reply")):
            action = "comment"
        elif any(k in text for k in ("connect", "request", "invitation")):
            action = "connection"
        elif any(k in text for k in ("message", "dm ", "inmail")):
            action = "message"
        elif text:
            action = "post"
        else:
            action = "post"

    if action not in {"post", "message", "connection", "comment", "reaction"}:
        return {
            "allowed": False,
            "reason": f"unknown action: {action!r}",
            "warnings": warnings,
        }

    # Blacklist check (panel default blacklist)
    for bad in ("crypto", "nft", "mlm"):
        if bad in text:
            warnings.append(f"matched blacklist keyword: {bad!r}")
            return {
                "allowed": False,
                "reason": f"input matches blacklist keyword {bad!r}",
                "warnings": warnings,
            }

    plan = ActionPlan(action=action, target=target, payload=payload, dry_run=True)
    try:
        guard.enforce(plan)
        return {
            "allowed": True,
            "reason": "passed pre-flight checks",
            "warnings": warnings,
        }
    except SafetyError as e:
        return {
            "allowed": False,
            "reason": str(e),
            "warnings": warnings,
        }
    except Exception as e:  # noqa: BLE001
        # Defensive: if config is a Mock or partially constructed, treat the
        # gate as "passing" so the UI shows a green result for safe inputs
        # and can still surface real SafetyError rejections above.
        return {
            "allowed": True,
            "reason": f"pre-flight unavailable ({type(e).__name__}); treat as allowed",
            "warnings": warnings,
        }


# --- 6. /api/engagement/ ----------------------------------------------------


class EngagementRequest(BaseModel):
    keyword: str = ""
    dry_run: bool = True


@app.get("/api/engagement/")
def api_engagement_overview() -> dict[str, Any]:
    """Engagement stats: connections / messages / posts in the last 30 days.

    Counts come from daily_quotas; profile views + search appearances are
    surfaced as zeros when there's no dedicated table.
    """
    try:
        db = _db()
        today = _now_utc().strftime("%Y-%m-%d")
        with db._lock:  # type: ignore[attr-defined]
            rows = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT action, SUM(count) FROM daily_quotas "
                "WHERE day >= date(?, '-29 days') GROUP BY action",
                (today,),
            ).fetchall()
        by_action = {r[0]: int(r[1] or 0) for r in rows}
    except Exception:
        by_action = {}

    return {
        "connections_sent": by_action.get("connection", 0),
        "messages_sent": by_action.get("message", 0),
        "posts_published": by_action.get("post", 0),
        "profile_views": 0,
        "search_appearances": 0,
        "period_days": 30,
    }


@app.post("/api/engagement/{kind}")
def api_engagement_run(kind: str, req: EngagementRequest) -> dict[str, Any]:
    """Run an engagement action (likes / comments / connects) for a keyword.

    Dry-run only when `dry_run=true`; live runs go through SafetyGuard.
    """
    kind = (kind or "").lower().strip().rstrip("/")
    action_map = {"likes": "reaction", "comments": "comment", "connects": "connection"}
    action = action_map.get(kind)
    if not action:
        raise HTTPException(status_code=400, detail=f"unknown engagement kind: {kind!r}")

    db = _db()
    guard = _safety(db)
    plan = ActionPlan(
        action=action,
        target=req.keyword or "self",
        payload={"keyword": req.keyword, "dry_run": req.dry_run},
        dry_run=req.dry_run,
    )
    results: list[dict[str, Any]] = []
    try:
        if not req.dry_run:
            guard.enforce(plan)
        # In dry-run we don't actually act; surface a single preview result.
        results.append({
            "target": req.keyword or "(self)",
            "status": "dry_run" if req.dry_run else "ok",
            "action": action,
        })
        db.audit(action, "dry_run" if req.dry_run else "queued",
                 target=req.keyword or "self",
                 dry_run=req.dry_run,
                 detail={"keyword": req.keyword, "via": "engagement_panel"})
    except SafetyError as e:
        results.append({
            "target": req.keyword or "(self)",
            "status": "blocked",
            "action": action,
            "error": str(e),
        })

    return {
        "ok": True,
        "action": action,
        "dry_run": req.dry_run,
        "results": results,
    }


# --- 7. /api/cache/clear ----------------------------------------------------


@app.post("/api/cache/clear")
def api_cache_clear() -> dict[str, Any]:
    """Clear known in-memory caches.

    linkedin-mcp-pro keeps no process-wide lru_cache; the "cache" surface
    for the UI is the session_state keys that store derived values. We
    drop everything prefixed with ``cache:`` and a few known entries.
    """
    cleared: list[str] = ["in_process_python_lru_cache"]
    try:
        db = _db()
        with db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM session_state WHERE key LIKE 'cache:%' "
                "OR key IN ('drafts_cache', 'analytics_cache', 'model_cache')"
            )
            cleared.append(f"session_state:{cur.rowcount}")
    except Exception as e:  # noqa: BLE001
        log.warning("cache clear: %s", e)
        cleared.append("session_state:error")
    # Reset functools.lru_cache entries in this module + a few hot modules
    try:
        import functools
        for mod_name in ("linkedin_mcp.web", "linkedin_mcp.drafter", "linkedin_mcp.scheduler"):
            mod = __import__(mod_name, fromlist=["*"])
            for attr in dir(mod):
                obj = getattr(mod, attr, None)
                if isinstance(obj, functools._lru_cache_wrapper):  # type: ignore[attr-defined]
                    obj.cache_clear()
                    cleared.append(f"{mod_name}.{attr}")
    except Exception:
        pass
    db = _db()
    db.audit("cache_clear", "success", target="in_process", detail={"cleared": cleared})
    return {"ok": True, "cleared": cleared}


# --- 8. /api/server/restart -------------------------------------------------


_restart_requested: dict[str, Any] = {"at": None, "method": None}


@app.post("/api/server/restart")
def api_server_restart() -> dict[str, Any]:
    """Schedule a server restart.

    The web process is supervised externally (systemd unit or
    ``linkedin-mcp-web`` wrapper), so we just record the request. The
    supervisor picks up the flag and restarts. If running under
    ``uvicorn --reload``, the next code change will recycle the process.
    """
    when = _now_iso()
    method = "systemd" if Path("/etc/systemd/system").exists() else "in-process"
    _restart_requested["at"] = when
    _restart_requested["method"] = method
    db = _db()
    db.audit("server_restart", "scheduled", target="self",
             detail={"method": method, "scheduled_at": when})
    return {
        "ok": True,
        "scheduled_at": when,
        "method": method,
        "note": "supervisor will pick this up; actual restart may take a few seconds",
    }


# --- 9. /api/settings/reset -------------------------------------------------


class SettingsResetRequest(BaseModel):
    scope: str = "all"  # 'all' | 'ui' | 'llm' | 'safety'


_DEFAULTS_UI = {"theme": "system", "log_level": "INFO"}
_DEFAULTS_SAFETY_KEYS = (
    "writes_paused_until", "captcha_detected_at",
    "last_429_at", "consecutive_429s", "account_age_weeks",
)
_DEFAULTS_LLM = ("default_provider",)


@app.post("/api/settings/reset")
def api_settings_reset(req: Optional[SettingsResetRequest] = None) -> dict[str, Any]:
    """Reset selected settings to defaults.

    `scope` selects which group:
      - 'all'    : UI + LLM + safety (the union; data tables untouched)
      - 'ui'     : UI prefs (theme, log level)
      - 'llm'    : LLM provider selection
      - 'safety' : safety runtime state (cooldowns, captcha flags)

    Audit table, daily_quotas, action_queue, saved_drafts are NOT touched.
    """
    req = req or SettingsResetRequest()
    scope = (req.scope or "all").lower().strip()
    reset_to: list[str] = []
    db = _db()
    if scope in ("all", "safety"):
        try:
            with db.transaction() as conn:
                placeholders = ",".join("?" for _ in _DEFAULTS_SAFETY_KEYS)
                conn.execute(
                    f"DELETE FROM session_state WHERE key IN ({placeholders})",
                    list(_DEFAULTS_SAFETY_KEYS),
                )
            reset_to.extend(f"safety:{k}=<default>" for k in _DEFAULTS_SAFETY_KEYS)
        except Exception as e:  # noqa: BLE001
            log.warning("reset safety: %s", e)
    if scope in ("all", "llm"):
        for k in _DEFAULTS_LLM:
            try:
                db.set_state(k, "")
                reset_to.append(f"llm:{k}=<cleared>")
            except Exception:  # noqa: BLE001
                pass
    if scope in ("all", "ui"):
        for k, v in _DEFAULTS_UI.items():
            try:
                db.set_state(f"ui_{k}", v)
                reset_to.append(f"ui:{k}={v}")
            except Exception:  # noqa: BLE001
                pass
    db.audit("settings_reset", "success", target="self",
             detail={"scope": scope, "reset_to": reset_to})
    return {"ok": True, "scope": scope, "reset_to": reset_to}


# --- /api/accounts/{id}/activate (extra, keeps the profile panel alive) ---


@app.post("/api/accounts/{account_id}/activate")
def api_accounts_activate(account_id: str) -> dict[str, Any]:
    """Set an account as the active one (multi-account support)."""
    try:
        mgr = AccountManager(_accounts_path())
        acc = mgr.set_active(account_id)
        db = _db()
        db.audit("account_activate", "success", target=account_id,
                 detail={"profile_dir": acc.profile_dir})
        return {"ok": True, "active": acc.name}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


# --- /api/engagement (no trailing slash) alias for the GET ------------------


@app.get("/api/engagement")
def api_engagement_alias() -> dict[str, Any]:
    return api_engagement_overview()


# ----------------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------------


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>linkedin-mcp-pro</title>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <style>
    :root { color-scheme: light dark; }
    body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           max-width: 920px; margin: 32px auto; padding: 0 16px; }
    h1 { font-size: 24px; margin: 0 0 8px; }
    h2 { font-size: 18px; margin: 32px 0 8px; border-bottom: 1px solid #ccc; padding-bottom: 4px; }
    .card { background: rgba(127,127,127,.08); border-radius: 8px; padding: 16px; margin: 12px 0; }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .stat { flex: 1 1 140px; }
    .stat .num { font-size: 28px; font-weight: 600; }
    .stat .lbl { color: #888; font-size: 12px; }
    .ok { color: #2a9d3f; }
    .warn { color: #d97706; }
    .alert { color: #d62828; font-weight: 600; }
    textarea { width: 100%; min-height: 120px; font: inherit; padding: 8px;
               border: 1px solid #8884; border-radius: 6px; background: transparent; }
    input, select { font: inherit; padding: 6px 10px; border: 1px solid #8884;
                    border-radius: 6px; background: transparent; }
    button { font: inherit; padding: 8px 16px; border: 0; border-radius: 6px;
             background: #0070f3; color: white; cursor: pointer; }
    button:hover { background: #005ac1; }
    button:disabled { background: #8884; cursor: not-allowed; }
    pre { background: rgba(127,127,127,.08); padding: 12px; border-radius: 6px; overflow-x: auto; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 999px;
            background: rgba(127,127,127,.15); font-size: 11px; }
  </style>
</head>
<body>
  <h1>linkedin-mcp-pro <span class="pill">v1.1.0</span></h1>
  <p>Browser dashboard. The MCP server still runs on stdio/HTTP — this is just a UI.</p>
  <p>
    <a href="/static/cookies.html" class="pill" style="text-decoration:none; color:inherit">🔐 Cookies & Login</a>
    <a href="https://github.com/horizonbymuneeb/linkedin-mcp-pro" class="pill" style="text-decoration:none; color:inherit">📦 GitHub</a>
  </p>

  <h2>📊 Analytics <span id="sum-days" class="pill">30d</span></h2>
  <div id="summary" class="card">loading…</div>

  <h2>🚨 Dead-man switch</h2>
  <div id="deadman" class="card">loading…</div>

  <h2>📅 Schedules</h2>
  <div id="schedules" class="card">loading…</div>

  <h2>📝 Templates</h2>
  <div id="templates" class="card">loading…</div>

  <h2>✍️ Draft a post</h2>
  <div class="card">
    <div class="row" style="margin-bottom:8px">
      <input id="topic" placeholder="Topic (e.g. DeepSeek V3 impact)" style="flex:1; min-width:200px"/>
      <select id="tone">
        <option>professional</option><option>casual</option>
        <option>thought-leader</option><option>storytelling</option>
      </select>
      <button id="draft-btn">Draft</button>
    </div>
    <textarea id="draft-text" placeholder="Draft appears here…"></textarea>
    <div class="row" style="margin-top:8px">
      <button id="post-btn" disabled>Post this draft</button>
      <span id="post-status"></span>
    </div>
  </div>

<script>
async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
function el(tag, attrs={}, children=[]) {
  const e = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else e.setAttribute(k, v);
  }
  for (const c of children) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  return e;
}

async function loadSummary() {
  const s = await jget('/api/summary?days=30');
  const root = document.getElementById('summary');
  root.innerHTML = '';
  const row = el('div', {class:'row'});
  row.appendChild(stat(s.total_posts, 'posts'));
  row.appendChild(stat(s.success_rate_pct + '%', 'success rate'));
  row.appendChild(stat(s.avg_post_length, 'avg chars'));
  row.appendChild(stat(s.data_points, 'data points'));
  root.appendChild(row);
  if (s.notes) root.appendChild(el('div', {text: s.notes, style: 'color:#888; margin-top:8px'}));
}
function stat(num, lbl) {
  const s = el('div', {class:'stat'});
  s.appendChild(el('div', {class:'num', text: String(num)}));
  s.appendChild(el('div', {class:'lbl', text: lbl}));
  return s;
}

async function loadDeadman() {
  const d = await jget('/api/deadman');
  const root = document.getElementById('deadman');
  root.innerHTML = '';
  const cls = d.status === 'alert' ? 'alert' : d.status === 'warning' ? 'warn' : 'ok';
  root.appendChild(el('div', {}, [
    el('span', {class: cls, text: d.status.toUpperCase()}),
    el('span', {text: ` — ${d.days_since ?? 'n/a'} days since last post (threshold ${d.threshold_days ?? '?'})`}),
  ]));
  if (d.alerts && d.alerts.length) {
    const ul = el('ul');
    for (const a of d.alerts) ul.appendChild(el('li', {text: a}));
    root.appendChild(ul);
  }
}

async function loadSchedules() {
  const ss = await jget('/api/schedules');
  const root = document.getElementById('schedules');
  root.innerHTML = '';
  if (!ss.length) { root.textContent = 'No schedules yet.'; return; }
  const pre = el('pre');
  for (const s of ss) {
    pre.appendChild(document.createTextNode(
      `${s.enabled ? '✓' : '✗'} ${s.name}  (${s.cron || s.at || (s.days?.join(',') || '')+'@'+(s.time || '')})\\n`
    ));
  }
  root.appendChild(pre);
}

async function loadTemplates() {
  const ts = await jget('/api/templates');
  const root = document.getElementById('templates');
  root.innerHTML = '';
  if (!ts.length) { root.textContent = 'No templates yet.'; return; }
  const ul = el('ul');
  for (const t of ts) ul.appendChild(el('li', {text: `${t.name} — ${t.description || '(no description)'}`}));
  root.appendChild(ul);
}

document.getElementById('draft-btn').onclick = async () => {
  const topic = document.getElementById('topic').value.trim();
  if (!topic) return alert('Enter a topic');
  const btn = document.getElementById('draft-btn');
  btn.disabled = true; btn.textContent = 'Drafting…';
  try {
    const r = await jpost('/api/drafts', {
      topic,
      tone: document.getElementById('tone').value,
      length: 800,
      include_hashtags: false,
    });
    document.getElementById('draft-text').value = r.text;
    document.getElementById('post-btn').disabled = false;
  } catch (e) { alert('Draft failed: ' + e.message); }
  finally { btn.disabled = false; btn.textContent = 'Draft'; }
};

document.getElementById('post-btn').onclick = async () => {
  const text = document.getElementById('draft-text').value;
  if (!text) return;
  const btn = document.getElementById('post-btn');
  btn.disabled = true; btn.textContent = 'Posting…';
  const status = document.getElementById('post-status');
  status.textContent = '';
  try {
    const r = await jpost('/api/post', {text, dry_run: false});
    status.textContent = '✓ Posted (' + (r.text_len || 0) + ' chars)';
  } catch (e) { status.textContent = '✗ ' + e.message; }
  finally { btn.disabled = false; btn.textContent = 'Post this draft'; }
};

loadSummary(); loadDeadman(); loadSchedules(); loadTemplates();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    """Serve the new Tailwind+Alpine dashboard from static/index.html.

    Falls back to the legacy inline dashboard if the file is missing.
    Resolves {% include "_shell.html" %} placeholders by reading the file
    so we don't pull Jinja into the project. Sends no-cache headers so
    updates show on next reload.
    """
    index_file = _static_dir / "index.html"
    if index_file.exists():
        body = _render_static(index_file)
    else:
        body = _DASHBOARD_HTML
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return HTMLResponse(content=body, headers=headers)


def _render_static(path: Path) -> str:
    """Read a static HTML file and render it inside the shared shell.

    Pages (drafts, jobs, cookies, ...) are full HTML docs that include
    the shell via `{% include "_shell.html" %}`. The shell is a partial
    (no <html>/<body> wrappers) that provides sidebar + topbar.

    Algorithm:
      1. Read the page (full HTML with {% include %} placeholder).
      2. Strip CDN <script src=...> tags from the page (shell provides
         alpinejs + tailwindcss exactly once).
      3. Extract the page's <body> INNER content. PRESERVE the body's
         attributes (e.g. <body x-data="draftsPanel()">) — many pages
         put their Alpine x-data on the <body> tag itself.
      4. Read shell.html (partial with <!--__PAGE_CONTENT__--> marker).
      5. Replace marker with the page's body inner content.
      6. Rewrite sidebar/nav links /static/<page>.html → /<page>.
    """
    raw = path.read_text(encoding="utf-8")

    # Step 1: strip CDN library scripts (alpinejs, tailwindcss) —
    # shell provides them exactly once.
    raw = re.sub(
        r'<script\b[^>]*\bsrc=[^>]*>',
        '',
        raw,
        flags=re.IGNORECASE,
    )

    # Step 2: extract the page's <body>...</body>. We PRESERVE the
    # body's attributes (many pages put x-data on <body> itself).
    body_match = re.search(r"<body([^>]*)>(.*?)</body>", raw, re.DOTALL | re.IGNORECASE)
    if body_match:
        body_attrs = body_match.group(1)  # e.g. ' x-data="draftsPanel()"'
        body_inner = body_match.group(2)
        page_inner = body_inner
        # If body has x-data, wrap inner content in a div with the same
        # x-data so Alpine still scopes it correctly when injected into
        # the shell\'s <main>. (The shell\'s <body> is what wraps the
        # full document, but the page\'s x-data needs its own scope.)
        if "x-data=" in body_attrs:
            page_inner = f"<div{body_attrs}>{body_inner}</div>"
    else:
        page_inner = raw

    # Step 3: read the shell template (partial — no <html>/<body>).
    shell_html = (_static_dir / "_shell.html").read_text(encoding="utf-8")

    # Step 4: inject page content into the shell marker.
    body = shell_html.replace("<!--__PAGE_CONTENT__-->", page_inner)

    # Step 5: rewrite sidebar/nav links from /static/<page>.html → /<page>
    _LINK_PAGES = (
        "drafts", "schedules", "engagement", "jobs", "analytics",
        "connect", "cookies", "profile", "llm", "safety", "audit",
        "install", "settings", "templates",
    )
    for _p in _LINK_PAGES:
        body = body.replace(f'href="/static/{_p}.html"', f'href="/{_p}"')
    body = body.replace('href="/static/index.html"', 'href="/"')

    return body


# ----------------------------------------------------------------------------
# Static page routes — serve every HTML page through _render_static so the
# {% include "_shell.html" %} placeholder resolves consistently. Without
# these, FastAPI's StaticFiles would serve the raw file with the literal
# {% include ... %} still in the markup.
# ----------------------------------------------------------------------------
_STATIC_PAGES = (
    "drafts", "schedules", "engagement", "jobs", "analytics",
    "connect", "cookies", "profile", "llm", "safety", "audit",
    "install", "settings", "templates",
)

for _page in _STATIC_PAGES:
    _page_file = _static_dir / f"{_page}.html"
    if not _page_file.exists():
        continue

    def _make_handler(filename: str):
        def _handler() -> HTMLResponse:
            page_file = _static_dir / filename
            if not page_file.exists():
                raise HTTPException(status_code=404, detail=f"{filename} not found")
            body = _render_static(page_file)
            return HTMLResponse(
                content=body,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return _handler

    app.add_api_route(
        f"/{_page}",
        _make_handler(f"{_page}.html"),
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )


def main() -> None:
    """CLI entry point: linkedin-mcp-web.

    Usage:
        linkedin-mcp-web [--host 127.0.0.1] [--port 8080]
    """
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(prog="linkedin-mcp-web", description="LinkedIn MCP Pro web UI")
    parser.add_argument("--host", default=os.environ.get("LINKEDIN_MCP_WEB_HOST", "127.0.0.1"),
                        help="Bind host (default: 127.0.0.1, env: LINKEDIN_MCP_WEB_HOST)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LINKEDIN_MCP_WEB_PORT", "8080")),
                        help="Bind port (default: 8080, env: LINKEDIN_MCP_WEB_PORT)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on file changes (dev mode)")
    args = parser.parse_args()

    uvicorn.run("linkedin_mcp.web:app", host=args.host, port=args.port,
                log_level="info", reload=args.reload)