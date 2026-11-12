"""Minimal async PostgREST client for Supabase.

Supabase exposes Postgres over PostgREST, so a plain HTTP client is all the
agent needs. That keeps the container image small.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings

_client: httpx.AsyncClient | None = None


def _headers() -> dict[str, str]:
    key = get_settings().supabase_service_key.get_secret_value()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=f"{settings.supabase_url.rstrip('/')}/rest/v1",
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
    return _client


async def rpc(function: str, params: dict[str, Any]) -> Any:
    resp = await get_client().post(f"/rpc/{function}", json=params, headers=_headers())
    resp.raise_for_status()
    return resp.json()


async def insert(table: str, row: dict[str, Any]) -> None:
    headers = {**_headers(), "Prefer": "return=minimal"}
    resp = await get_client().post(f"/{table}", json=row, headers=headers)
    resp.raise_for_status()


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
