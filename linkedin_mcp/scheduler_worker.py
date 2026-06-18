"""Worker that drains the post queue created by the scheduler.

Called by systemd/linkedin-mcp-scheduler.service (or manually via
``linkedin-mcp scheduler run-due && python -m linkedin_mcp.scheduler_worker``).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import Optional

from .config import load_config
from .db import DB
from .safety import ActionPlan, SafetyError, SafetyGuard

log = logging.getLogger("linkedin_mcp.scheduler_worker")


def _get_create_post():
    """Lazy import to avoid circular deps."""
    try:
        from .api.posts import create_post  # type: ignore
        return create_post
    except Exception:
        try:
            from .tools.posts import create_post  # type: ignore
            return create_post
        except Exception:
            return None


def process_due_posts(
    db: DB,
    cfg=None,
    safety: Optional[SafetyGuard] = None,
    max_per_run: int = 5,
) -> dict:
    """Drain up to ``max_per_run`` pending post actions from the queue.

    Returns a summary dict: ``{"processed": n, "succeeded": n, "failed": n, "results": [...]}``.
    """
    if cfg is None:
        cfg = load_config()
    if safety is None:
        safety = SafetyGuard(cfg, db)
    create_post = _get_create_post()
    summary = {"processed": 0, "succeeded": 0, "failed": 0, "results": []}
    for _ in range(max_per_run):
        item = db.pop_due()
        if not item:
            break
        summary["processed"] += 1
        try:
            payload = json.loads(item["payload"])
            text = payload.get("text") or ""
            template = payload.get("template")
            if template:
                # Render via templates store
                try:
                    from .templates import TemplatesStore
                    text = TemplatesStore().render(
                        template, payload.get("vars") or {}
                    )
                except Exception as e:
                    log.warning("Template render failed for %s: %s", template, e)
            if not text:
                raise ValueError(
                    f"Queue item {item['id']} has no text and no renderable template"
                )
            plan = ActionPlan(
                action="post",
                target="self",
                payload={"text": text},
            )
            safety.enforce(plan)
            # Jitter before posting
            jitter = safety.jitter_seconds(cfg)
            time.sleep(jitter)
            if create_post is None:
                raise RuntimeError("create_post tool not available")
            result = create_post(text=text, dry_run=False)
            db.complete_queue(item["id"], "done", result=result)
            summary["succeeded"] += 1
            summary["results"].append({"id": item["id"], "status": "done", "result": result})
        except SafetyError as e:
            db.complete_queue(item["id"], "failed", error=str(e))
            summary["failed"] += 1
            summary["results"].append({"id": item["id"], "status": "failed", "error": str(e)})
            log.warning("Queue item %d rejected by safety: %s", item["id"], e)
        except Exception as e:
            db.complete_queue(item["id"], "failed", error=str(e))
            summary["failed"] += 1
            summary["results"].append({"id": item["id"], "status": "failed", "error": str(e)})
            log.exception("Queue item %d failed: %s", item["id"], e)
    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Drain scheduled post queue")
    parser.add_argument("--max", type=int, default=5, help="max posts to process per run")
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except Exception as e:
        print(f"❌ Could not load config: {e}", file=sys.stderr)
        return 1
    db = DB(cfg.storage.db_path)
    summary = process_due_posts(db, cfg=cfg, max_per_run=args.max)
    print(f"Processed: {summary['processed']}, Succeeded: {summary['succeeded']}, Failed: {summary['failed']}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())