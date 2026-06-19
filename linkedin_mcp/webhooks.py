"""Webhook integrations for linkedin-mcp-pro (v1.0.0).

Fire HTTP webhooks when specific events happen:
  - post.success
  - post.failed
  - shadowban.alert
  - deadman.alert
  - schedule.fired

Configuration lives at ``~/.linkedin-mcp/webhooks.yaml``. Webhooks
fire-and-forget via urllib (10s timeout). Failures are logged but
never block the main action.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml


VALID_EVENTS = {
    "post.success",
    "post.failed",
    "shadowban.alert",
    "deadman.alert",
    "schedule.fired",
}


class WebhookError(Exception):
    """Raised for any webhook-config failure."""


@dataclass
class Webhook:
    name: str
    url: str
    events: list[str]
    secret: str = ""
    enabled: bool = True
    last_status: Optional[int] = None
    last_fired_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "events": list(self.events),
            "secret": "***" if self.secret else "",
            "enabled": self.enabled,
            "last_status": self.last_status,
            "last_fired_at": self.last_fired_at,
        }


class WebhookManager:
    """File-backed webhook registry."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(
            path
            or os.environ.get("LINKEDIN_MCP_WEBHOOKS_FILE")
            or (Path.home() / ".linkedin-mcp" / "webhooks.yaml")
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"webhooks": []}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {"webhooks": []}
        except yaml.YAMLError as e:
            raise WebhookError(f"Invalid YAML in {self.path}: {e}") from e

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, width=120)

    def list_webhooks(self) -> list[Webhook]:
        data = self._read()
        out: list[Webhook] = []
        for raw in data.get("webhooks", []) or []:
            # Don't persist the secret in plain text
            out.append(
                Webhook(
                    name=str(raw.get("name", "")),
                    url=str(raw.get("url", "")),
                    events=list(raw.get("events", []) or []),
                    secret="",  # never read secret back
                    enabled=bool(raw.get("enabled", True)),
                    last_status=raw.get("last_status"),
                    last_fired_at=raw.get("last_fired_at"),
                )
            )
        return out

    def add(self, name: str, url: str, events: list[str], secret: str = "") -> Webhook:
        if not name or not name.strip():
            raise WebhookError("Webhook name is required")
        if not url or not url.strip():
            raise WebhookError(f"Webhook {name!r} needs a url")
        bad = [e for e in events if e not in VALID_EVENTS]
        if bad:
            raise WebhookError(
                f"Webhook {name!r} has invalid events {bad}; "
                f"valid: {sorted(VALID_EVENTS)}"
            )
        current = self.list_webhooks()
        if any(w.name == name for w in current):
            raise WebhookError(f"Webhook {name!r} already exists")
        wh = Webhook(name=name, url=url, events=events, secret=secret)
        current.append(wh)
        # Don't write secret to disk
        self._write({"webhooks": [_public_dict(w) for w in current]})
        return wh

    def remove(self, name: str) -> bool:
        current = self.list_webhooks()
        new = [w for w in current if w.name != name]
        if len(new) == len(current):
            return False
        self._write({"webhooks": [_public_dict(w) for w in new]})
        return True

    def fire(self, event: str, payload: dict[str, Any], *, async_: bool = True) -> list[dict[str, Any]]:
        """Fire all webhooks subscribed to ``event``.

        Returns a list of result dicts: ``{name, status, error}``.
        """
        results: list[dict[str, Any]] = []
        for wh in self.list_webhooks():
            if not wh.enabled or event not in wh.events:
                continue
            if async_:
                threading.Thread(
                    target=_send_one,
                    args=(wh, event, payload, results),
                    daemon=True,
                ).start()
            else:
                _send_one(wh, event, payload, results)
        return results


def _public_dict(w: Webhook) -> dict[str, Any]:
    return {
        "name": w.name,
        "url": w.url,
        "events": list(w.events),
        "enabled": w.enabled,
        "last_status": w.last_status,
        "last_fired_at": w.last_fired_at,
    }


def _send_one(wh: Webhook, event: str, payload: dict[str, Any], results: list[dict[str, Any]]) -> None:
    body = json.dumps(
        {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "payload": payload,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        wh.url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "linkedin-mcp-pro/1.0 webhook"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            wh.last_status = resp.status
            wh.last_fired_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            results.append({"name": wh.name, "status": resp.status})
    except urllib.error.HTTPError as e:
        wh.last_status = e.code
        results.append({"name": wh.name, "status": e.code, "error": str(e)})
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        results.append({"name": wh.name, "status": None, "error": str(e)})