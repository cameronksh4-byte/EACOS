"""RLS and repository behaviour against a real Postgres.

Skipped unless ``TRIAGE_TEST_PG_ADMIN_URL`` points at a superuser connection
of a disposable server, e.g.::

    TRIAGE_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:55432/postgres pytest tests/integration
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from conftest import make_bol, make_factura
from triage_engine.services.reconciliation import build_triage_packet
from triage_engine.storage.audit import AuditEventType, AuditLogger, InMemoryObjectStore
from triage_engine.storage.db import tenant_transaction
from triage_engine.storage.repository import PostgresChainHeadStore, PostgresTriageRepository

ADMIN_URL = os.environ.get("TRIAGE_TEST_PG_ADMIN_URL")
pytestmark = [
    pytest.mark.skipif(not ADMIN_URL, reason="TRIAGE_TEST_PG_ADMIN_URL not set"),
    pytest.mark.anyio,
]
ROOT = Path(__file__).resolve().parents[2]
TENANT_A = uuid4()
TENANT_B = uuid4()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    assert ADMIN_URL
    db_name = f"triage_test_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(16)
    admin = await asyncpg.connect(ADMIN_URL)
    await admin.execute(f'CREATE DATABASE "{db_name}"')
    await admin.close()

    db_admin = await asyncpg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{db_name}")
    try:
        await db_admin.execute((ROOT / "tests/integration/supabase_shim.sql").read_text())
        await db_admin.execute(
            f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'triage_backend') THEN
                    CREATE ROLE triage_backend LOGIN NOINHERIT;
                END IF;
            END $$;
            ALTER ROLE triage_backend PASSWORD '{password}';
            GRANT authenticated TO triage_backend;
            GRANT CONNECT ON DATABASE "{db_name}" TO triage_backend;
            """
        )
        await db_admin.execute((ROOT / "migrations/001_init.sql").read_text())
        await db_admin.execute("INSERT INTO auth.users (id) VALUES ($1), ($2)", TENANT_A, TENANT_B)
        await db_admin.execute(
            "INSERT INTO private.channel_routes (channel, external_id, tenant_id) VALUES "
            "('whatsapp', '1065550001', $1), ('email', 'docs@b.example.com', $2)",
            TENANT_A,
            TENANT_B,
        )
    finally:
        await db_admin.close()

    host_part = ADMIN_URL.split("@", 1)[1].rsplit("/", 1)[0]
    backend_pool = await asyncpg.create_pool(
        f"postgresql://triage_backend:{password}@{host_part}/{db_name}", min_size=1, max_size=4
    )
    try:
        yield backend_pool
    finally:
        await backend_pool.close()
        admin = await asyncpg.connect(ADMIN_URL)
        await admin.execute(f'DROP DATABASE "{db_name}" WITH (FORCE)')
        await admin.close()


async def test_channel_routing(pool: asyncpg.Pool) -> None:
    repo = PostgresTriageRepository(pool)
    assert await repo.resolve_tenant("whatsapp", "1065550001") == TENANT_A
    assert await repo.resolve_tenant("email", "DOCS@B.EXAMPLE.COM") == TENANT_B
    assert await repo.resolve_tenant("email", "nobody@x.com") is None


async def test_backend_role_cannot_read_tables_without_tenant_context(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM public.inbound_messages")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM private.channel_routes")


async def test_rls_isolates_tenants(pool: asyncpg.Pool) -> None:
    repo = PostgresTriageRepository(pool)
    inbound = await repo.record_inbound(TENANT_A, "whatsapp", "wamid.1", "a" * 64, None)
    assert inbound is not None
    assert await repo.record_inbound(TENANT_A, "whatsapp", "wamid.1", "a" * 64, None) is None

    factura = make_factura()
    await repo.save_document(TENANT_A, inbound, factura, "b" * 64)

    # Tenant B cannot see, pair with, or update tenant A's rows.
    assert await repo.find_counterpart(TENANT_B, make_bol()) is None
    found = await repo.find_counterpart(TENANT_A, make_bol())
    assert found is not None and found.document == factura
    async with tenant_transaction(pool, TENANT_B) as conn:
        assert await conn.fetchval("SELECT count(*) FROM public.shipping_documents") == 0
        result = await conn.execute(
            "UPDATE public.inbound_messages SET status = 'failed' WHERE id = $1", inbound
        )
        assert result == "UPDATE 0"
        # Writing a row for another tenant is refused by WITH CHECK.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO public.inbound_messages "
                "(tenant_id, channel, external_message_id, payload_sha256) "
                "VALUES ($1, 'email', 'x', $2)",
                TENANT_A,
                "c" * 64,
            )


async def test_packet_pairing_round_trip(pool: asyncpg.Pool) -> None:
    repo = PostgresTriageRepository(pool)
    inbound = await repo.record_inbound(TENANT_A, "email", "<m@x>", "a" * 64, None)
    assert inbound is not None
    factura, bol = make_factura(), make_bol()
    factura_id = await repo.save_document(TENANT_A, inbound, factura, "b" * 64)
    bol_id = await repo.save_document(TENANT_A, inbound, bol, "c" * 64)
    packet = build_triage_packet(tenant_id=TENANT_A, factura=factura, bol=bol)
    await repo.save_packet(TENANT_A, packet, factura_id, bol_id)
    # Paired documents are no longer offered as counterparts.
    assert await repo.find_counterpart(TENANT_A, make_bol()) is None


async def test_audit_chain_head_and_append_only_mirror(pool: asyncpg.Pool) -> None:
    objects = InMemoryObjectStore()
    audit = AuditLogger(objects, PostgresChainHeadStore(pool))
    for _ in range(3):
        await audit.record(
            tenant_id=TENANT_A, event_type=AuditEventType.WEBHOOK_RECEIVED, actor="test"
        )
    await audit.record(tenant_id=TENANT_B, event_type=AuditEventType.WEBHOOK_RECEIVED, actor="t")

    async with tenant_transaction(pool, TENANT_A) as conn:
        assert await conn.fetchval("SELECT last_sequence FROM public.audit_chain_heads") == 3
        assert await conn.fetchval("SELECT count(*) FROM public.audit_events") == 3
    async with tenant_transaction(pool, TENANT_A) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM public.audit_events")
    async with tenant_transaction(pool, TENANT_A) as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("UPDATE public.audit_chain_heads SET last_sequence = 1")
