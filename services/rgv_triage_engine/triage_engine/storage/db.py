"""Supabase Postgres access with Row-Level Security enforced per tenant.

The service connects with a dedicated login role, but every unit of work
runs inside a transaction that:

1. sets ``request.jwt.claims`` to ``{"sub": <tenant_id>, "role": "authenticated"}``
   (transaction-local), which is exactly what Supabase's ``auth.uid()``
   reads; and
2. switches to the ``authenticated`` role with ``SET LOCAL ROLE``, which has
   no ``BYPASSRLS`` attribute.

The RLS policies (``tenant_id = auth.uid()``) therefore apply to the
backend's own queries. A bug that forgets a ``WHERE tenant_id = ...`` clause
can still only ever see or write the current tenant's rows. Both settings
are transaction-scoped, so they cannot leak to the next borrower of a pooled
connection.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import asyncpg

from triage_engine.config import Settings


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Create the asyncpg pool with TLS certificate verification enabled.

    Raises:
        RuntimeError: If no database URL is configured.
    """
    dsn = settings.require_secret("database_url")
    ssl_context = ssl.create_default_context(cafile=settings.database_ssl_root_cert)
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED

    kwargs: dict[str, Any] = {
        "dsn": dsn,
        "ssl": ssl_context,
        "min_size": settings.database_pool_min_size,
        "max_size": settings.database_pool_max_size,
        "command_timeout": 30,
        "server_settings": {"application_name": "rgv-triage-engine"},
    }
    if settings.database_uses_transaction_pooler:
        kwargs["statement_cache_size"] = 0
    return await asyncpg.create_pool(**kwargs)


@asynccontextmanager
async def tenant_transaction(pool: asyncpg.Pool, tenant_id: UUID) -> AsyncIterator[Any]:
    """Yield a connection inside an RLS-scoped transaction for ``tenant_id``.

    Args:
        pool: Connection pool.
        tenant_id: The tenant (Supabase ``auth.users.id``) to act as.
    """
    claims = json.dumps({"sub": str(tenant_id), "role": "authenticated"})
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", claims)
        await conn.execute("SET LOCAL ROLE authenticated")
        yield conn


async def resolve_channel_tenant(pool: asyncpg.Pool, channel: str, external_id: str) -> UUID | None:
    """Map an inbound channel address to its tenant.

    Uses a ``SECURITY DEFINER`` function in a schema not exposed through the
    Supabase Data API; ``authenticated`` and ``anon`` cannot call it.
    """
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT private.resolve_channel_tenant($1, $2)", channel, external_id
        )
    return value if isinstance(value, UUID) else (UUID(str(value)) if value else None)
