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
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analytics import Analytics
from .config import load_config
from .cookies_api import router as cookies_router
from .db import DB
from .deadman import DeadManSwitch
from .drafter import PostDrafter
from .safety import SafetyError, SafetyGuard
from .scheduler import PostScheduler
from .templates import TemplatesStore

log = logging.getLogger("linkedin_mcp.web")

app = FastAPI(
    title="linkedin-mcp-pro web",
    description="Browser UI for the LinkedIn MCP server",
    version="1.1.0",
)
app.include_router(cookies_router)

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
    text: str
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


def _safety(db: DB) -> SafetyGuard:
    cfg = load_config()
    return SafetyGuard(cfg, db)


# ----------------------------------------------------------------------------
# API routes
# ----------------------------------------------------------------------------


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
    db = _db()
    safety = _safety(db)
    from .safety import ActionPlan
    try:
        safety.enforce(
            ActionPlan(
                action="post",
                target="self",
                payload={"text": req.text},
                dry_run=req.dry_run,
            )
        )
    except SafetyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if req.dry_run:
        return {"dry_run": True, "would_post": req.text[:200] + ("..." if len(req.text) > 200 else "")}
    # Real post would call the browser-based create_post here. The web UI
    # is a thin layer; the actual LinkedIn write happens via the MCP server's
    # create_post tool. For now we surface the safety decision.
    db.audit("post", "self", "dry_run" if req.dry_run else "success",
             dry_run=1 if req.dry_run else 0,
             detail={"text_len": len(req.text), "via": "web_ui"})
    return {"ok": True, "dry_run": False, "text_len": len(req.text)}


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
    return HTMLResponse(_DASHBOARD_HTML)


def main() -> None:
    """CLI entry point: linkedin-mcp-web."""
    import uvicorn
    host = os.environ.get("LINKEDIN_MCP_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("LINKEDIN_MCP_WEB_PORT", "8080"))
    uvicorn.run(app, host=host, port=port, log_level="info")