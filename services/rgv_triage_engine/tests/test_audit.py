"""Hash-chained audit log and S3 Object Lock writes."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
import pytest
from botocore.exceptions import ClientError

from conftest import TENANT
from triage_engine.config import MIN_CBP_RETENTION_DAYS, Settings
from triage_engine.storage.audit import (
    GENESIS_HASH,
    AuditEventType,
    AuditIntegrityError,
    AuditLogger,
    InMemoryChainHeadStore,
    InMemoryObjectStore,
    ObjectExistsError,
    S3ObjectLockStore,
    compute_record_hash,
    verify_chain,
)


def _records(store: InMemoryObjectStore) -> list[dict[str, Any]]:
    return [json.loads(store.objects[k]) for k in sorted(store.objects)]


async def _append(audit: AuditLogger, n: int) -> None:
    for i in range(n):
        await audit.record(
            tenant_id=TENANT,
            event_type=AuditEventType.WEBHOOK_RECEIVED,
            actor="test",
            details={"i": i},
        )


def test_chain_links_and_verifies(audit: AuditLogger, object_store: InMemoryObjectStore) -> None:
    anyio.run(_append, audit, 3)
    records = _records(object_store)
    assert [r["sequence"] for r in records] == [1, 2, 3]
    assert records[0]["prev_hash"] == GENESIS_HASH
    assert records[1]["prev_hash"] == records[0]["record_hash"]
    verify_chain(records)


@pytest.mark.parametrize(
    "tamper",
    [
        lambda rs: rs[1]["details"].update({"i": 99}),
        lambda rs: rs.pop(1),
        lambda rs: rs.reverse(),
        lambda rs: rs[2].update({"prev_hash": "f" * 64}),
    ],
)
def test_tampering_is_detected(
    audit: AuditLogger, object_store: InMemoryObjectStore, tamper: Any
) -> None:
    anyio.run(_append, audit, 3)
    records = _records(object_store)
    tamper(records)
    with pytest.raises(AuditIntegrityError):
        verify_chain(records)


def test_concurrent_writers_produce_a_single_chain(
    audit: AuditLogger, object_store: InMemoryObjectStore
) -> None:
    async def main() -> None:
        async with anyio.create_task_group() as tg:
            for _ in range(20):
                tg.start_soon(_append, audit, 1)

    anyio.run(main)
    verify_chain(_records(object_store))
    assert len(object_store.objects) == 20


def test_orphaned_s3_record_is_adopted() -> None:
    """S3 write succeeded but the DB commit did not: the next writer adopts it."""
    objects = InMemoryObjectStore()
    first = AuditLogger(objects, InMemoryChainHeadStore())
    anyio.run(_append, first, 1)
    orphan_key = next(iter(objects.objects))

    # A fresh head store simulates the rolled-back head (sequence 0).
    second = AuditLogger(objects, InMemoryChainHeadStore())
    record = anyio.run(
        lambda: second.record(
            tenant_id=TENANT, event_type=AuditEventType.DOCUMENT_PARSED, actor="test"
        )
    )
    assert record["sequence"] == 2
    assert orphan_key in objects.objects
    verify_chain(_records(objects))


def test_conflicting_object_raises() -> None:
    objects = InMemoryObjectStore()
    logger = AuditLogger(objects, InMemoryChainHeadStore())
    objects.objects[logger.key_for(TENANT, 1)] = json.dumps(
        {"sequence": 1, "prev_hash": "a" * 64, "record_hash": "b" * 64}
    ).encode()
    with pytest.raises(AuditIntegrityError, match="conflicting"):
        anyio.run(_append, logger, 1)


class _FakeS3:
    def __init__(self, fail_with: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_with = fail_with

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail_with:
            raise ClientError({"Error": {"Code": self.fail_with}}, "PutObject")
        return {}

    def get_object_lock_configuration(self, Bucket: str) -> dict[str, Any]:
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def get_bucket_versioning(self, Bucket: str) -> dict[str, Any]:
        return {"Status": "Enabled"}


def test_s3_put_uses_compliance_mode_conditional_write_and_checksum() -> None:
    s3 = _FakeS3()
    store = S3ObjectLockStore(s3, "audit-bucket", MIN_CBP_RETENTION_DAYS, kms_key_id="alias/audit")
    body = b'{"x":1}'
    anyio.run(store.put_record, "audit/v1/k.json", body)
    call = s3.calls[0]
    assert call["ObjectLockMode"] == "COMPLIANCE"
    assert call["IfNoneMatch"] == "*"
    assert call["ChecksumSHA256"] == base64.b64encode(hashlib.sha256(body).digest()).decode()
    assert call["ServerSideEncryption"] == "aws:kms"
    assert call["ObjectLockRetainUntilDate"] >= datetime.now(UTC) + timedelta(days=1826)
    anyio.run(store.assert_object_lock_enabled)


def test_s3_precondition_failure_maps_to_object_exists() -> None:
    store = S3ObjectLockStore(_FakeS3(fail_with="PreconditionFailed"), "b", MIN_CBP_RETENTION_DAYS)
    with pytest.raises(ObjectExistsError):
        anyio.run(store.put_record, "k", b"{}")


def test_retention_below_cbp_minimum_is_refused() -> None:
    with pytest.raises(ValueError, match=r"163\.4"):
        Settings(audit_retention_days=365)


def test_record_hash_excludes_itself() -> None:
    record = {"a": 1, "record_hash": "ignored"}
    assert compute_record_hash(record) == compute_record_hash({"a": 1})
