"""FastAPI router for LLM key management endpoints."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .llm_keys import (
    PROVIDERS,
    add_provider,
    fetch_models,
    get_default,
    get_provider,
    list_providers,
    remove_provider,
    set_default,
    check_provider,
)

router = APIRouter(prefix="/api/llm", tags=["llm"])


class AddProviderBody(BaseModel):
    name: str = Field(..., description="Provider name (openai, anthropic, etc.)")
    key: str | None = None
    base_url: str | None = None
    model: str | None = None


class SetDefaultBody(BaseModel):
    name: str


@router.get("/providers")
def get_providers() -> dict[str, Any]:
    return {"providers": list_providers()}


@router.get("/providers/{name}")
def get_one(name: str) -> dict[str, Any]:
    if name not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider: {name}")
    cfg = get_provider(name)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"{name} not configured")
    # Mask the key for the response
    masked = dict(cfg)
    if "key" in masked:
        from .llm_keys import mask_key
        masked["masked_key"] = mask_key(masked.pop("key", ""))
    return masked


@router.post("/providers")
def add(body: AddProviderBody) -> dict[str, Any]:
    if body.name not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unknown provider: {body.name}")
    try:
        cfg = add_provider(
            name=body.name,
            key=body.key,
            base_url=body.base_url,
            model=body.model,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return cfg


@router.delete("/providers/{name}")
def delete(name: str) -> dict[str, Any]:
    if name not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider: {name}")
    return {"ok": remove_provider(name)}


@router.post("/providers/{name}/test")
def test(name: str) -> dict[str, Any]:
    if name not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider: {name}")
    return check_provider(name)


@router.post("/test-all")
def test_all() -> dict[str, Any]:
    providers = list_providers()
    configured = [p for p in providers if p.get("is_configured")]
    if not configured:
        return {"results": []}

    def _run(p: dict) -> dict:
        return {"provider": p["name"], **check_provider(p["name"])}

    with ThreadPoolExecutor(max_workers=min(6, len(configured))) as ex:
        results = list(ex.map(_run, configured))
    return {"results": results}


@router.get("/default")
def read_default() -> dict[str, Any]:
    return {"default": get_default()}


@router.post("/default")
def write_default(body: SetDefaultBody) -> dict[str, Any]:
    if body.name not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unknown provider: {body.name}")
    set_default(body.name)
    return {"default": body.name}


@router.get("/models/{name}")
def models(name: str) -> dict[str, Any]:
    if name not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider: {name}")
    return fetch_models(name)
