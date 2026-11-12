"""Minimal async PostgREST client for Supabase.

Supabase exposes Postgres over PostgREST, so a plain HTTP client is all the
agent needs, which keeps the container image small. Callers always pass
tenant_id explicitly, and the SQL functions filter on it.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings

_client: httpx.AsyncClient | None = None


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    key = get_settings().supabase_service_key.get_secret_value()
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if extra:
        headers.update(extra)
    return headers


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=f"{settings.supabase_url.rstrip('/')}/rest/v1",
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
    return _client


def eq(value: Any) -> str:
    return f"eq.{value}"


async def rpc(function: str, params: dict[str, Any]) -> Any:
    resp = await get_client().post(f"/rpc/{function}", json=params, headers=_headers())
    resp.raise_for_status()
    return resp.json()


async def insert(table: str, row: dict[str, Any] | list[dict[str, Any]]) -> None:
    resp = await get_client().post(f"/{table}", json=row, headers=_headers({"Prefer": "return=minimal"}))
    resp.raise_for_status()


async def upsert(table: str, row: dict[str, Any] | list[dict[str, Any]], on_conflict: str) -> None:
    resp = await get_client().post(
        f"/{table}",
        params={"on_conflict": on_conflict},
        json=row,
        headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
    )
    resp.raise_for_status()


async def select(table: str, filters: dict[str, str], columns: str = "*", limit: int | None = None) -> list[dict]:
    params = {"select": columns, **filters}
    if limit is not None:
        params["limit"] = str(limit)
    resp = await get_client().get(f"/{table}", params=params, headers=_headers())
    resp.raise_for_status()
    return resp.json()


async def update(table: str, filters: dict[str, str], values: dict[str, Any]) -> list[dict]:
    resp = await get_client().patch(
        f"/{table}", params=filters, json=values, headers=_headers({"Prefer": "return=representation"})
    )
    resp.raise_for_status()
    return resp.json()


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
