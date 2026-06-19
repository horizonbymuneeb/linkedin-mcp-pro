"""MCP tools for webhooks (v1.0.0)."""

from __future__ import annotations

from typing import Any

from ..webhooks import Webhook, WebhookManager


def list_webhooks() -> list[dict[str, Any]]:
    return [w.to_dict() for w in WebhookManager().list_webhooks()]


def add_webhook(name: str, url: str, events: list[str], secret: str = "") -> dict[str, Any]:
    return {"ok": True, "webhook": WebhookManager().add(name, url, events, secret).to_dict()}


def remove_webhook(name: str) -> dict[str, Any]:
    if not WebhookManager().remove(name):
        raise ValueError(f"Webhook {name!r} not found")
    return {"ok": True, "removed": name}


def fire_webhook(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"fired": True, "event": event, "results": WebhookManager().fire(event, payload, async_=False)}