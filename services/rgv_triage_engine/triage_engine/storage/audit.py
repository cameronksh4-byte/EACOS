"""Immutable, hash-chained audit trail on S3 Object Lock (Compliance mode).

Design
------
* Each tenant has its own append-only chain. Record ``n`` stores the SHA-256
  of record ``n-1`` (``prev_hash``) and its own ``record_hash`` computed over
  a canonical JSON encoding. Altering, removing or reordering any record
  breaks every later hash, which :func:`verify_chain` detects.
* Each record is written as its own S3 object with Object Lock in
  **COMPLIANCE** mode and a retain-until date at least five years out
  (19 CFR 163.4). In Compliance mode no principal, including the root
  account, can delete or overwrite the object version before that date.
* Writes are conditional (``If-None-Match: *``), so an existing sequence
  number can never be silently replaced, and carry an S3-verified SHA-256
  checksum so a corrupted upload is refused by S3.
* The chain head (last sequence + hash) lives in Postgres and is locked with
  ``SELECT ... FOR UPDATE``, which serialises writers across replicas.
* Records hold metadata and content digests only: never raw document text
  or personal identifiers. That supports the NIST AI RMF *Measure* and
  *Manage* functions (traceability of every model call: model ID, prompt
  version, input/output digests, validation outcome) without turning the
  audit store into a second copy of regulated data.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

import anyio

GENESIS_HASH = "0" * 64
AUDIT_SCHEMA_VERSION = 1


class AuditEventType(StrEnum):
    """Audited event kinds."""

    WEBHOOK_RECEIVED = "webhook.received"
    WEBHOOK_REJECTED = "webhook.rejected"
    EMAIL_AUTH_VERDICT = "email.auth_verdict"
    DOCUMENT_PARSED = "document.parsed"
    LLM_EXTRACTION = "llm.extraction"
    LLM_EXTRACTION_FAILED = "llm.extraction_failed"
    TRIAGE_PACKET_CREATED = "triage.packet_created"
    PROCESSING_FAILED = "processing.failed"
    BROKER_REVIEW_RECORDED = "broker.review_recorded"
    BROKER_REVIEW_DENIED = "broker.review_denied"


class AuditIntegrityError(Exception):
    """The audit chain is broken or a write could not be made durable."""


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (UUID, StrEnum)):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def canonical_json(data: Mapping[str, Any]) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8."""
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default
    ).encode("utf-8")


def compute_record_hash(record: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical encoding of every field except ``record_hash``."""
    body = {k: v for k, v in record.items() if k != "record_hash"}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def verify_chain(records: list[Mapping[str, Any]]) -> None:
    """Verify an ordered list of records from one tenant chain.

    Raises:
        AuditIntegrityError: At the first gap, broken link or bad hash.
    """
    expected_prev = GENESIS_HASH
    for position, record in enumerate(records, start=1):
        if record["sequence"] != position:
            raise AuditIntegrityError(f"sequence gap at {position}")
        if record["prev_hash"] != expected_prev:
            raise AuditIntegrityError(f"broken link at sequence {position}")
        if compute_record_hash(record) != record["record_hash"]:
            raise AuditIntegrityError(f"hash mismatch at sequence {position}")
        expected_prev = record["record_hash"]


@dataclass(slots=True)
class ChainHead:
    """Mutable view of a tenant's chain head while it is locked."""

    tenant_id: UUID
    sequence: int
    last_hash: str
    #: (sequence, record_hash) pairs appended while the lock was held.
    appended: list[tuple[int, str]] = field(default_factory=list)

    def advance(self, sequence: int, record_hash: str) -> None:
        """Move the head forward by one record."""
        if sequence != self.sequence + 1:
            raise AuditIntegrityError(f"non-contiguous sequence {sequence}")
        self.sequence = sequence
        self.last_hash = record_hash
        self.appended.append((sequence, record_hash))


class ChainHeadStore(Protocol):
    """Serialises access to each tenant's chain head."""

    def locked(self, tenant_id: UUID) -> AbstractAsyncContextManager[ChainHead]:
        """Lock the head; persist ``sequence``/``last_hash`` on clean exit."""
        ...


class AuditObjectStore(Protocol):
    """WORM storage for audit records."""

    async def put_record(self, key: str, body: bytes) -> None:
        """Write a record once. Raise :class:`ObjectExistsError` if the key exists."""
        ...

    async def get_record(self, key: str) -> bytes:
        """Read a record back."""
        ...


class ObjectExistsError(Exception):
    """The conditional write failed because the key already exists."""


@dataclass(frozen=True, slots=True)
class ArchivedObject:
    """Location and digest of a record archived under Object Lock."""

    key: str
    sha256: str


class S3ObjectLockStore:
    """Writes audit records to an S3 bucket with Object Lock enabled.

    Args:
        s3_client: A ``boto3`` S3 client.
        bucket: Bucket created with Object Lock enabled.
        retention_days: Compliance-mode retention per object.
        kms_key_id: Optional customer-managed KMS key for SSE-KMS.
    """

    def __init__(
        self,
        s3_client: Any,
        bucket: str,
        retention_days: int,
        kms_key_id: str | None = None,
    ) -> None:
        self._s3 = s3_client
        self._bucket = bucket
        self._retention = timedelta(days=retention_days)
        self._kms_key_id = kms_key_id

    async def assert_object_lock_enabled(self) -> None:
        """Fail fast if the bucket cannot provide WORM guarantees."""

        def _check() -> None:
            config = self._s3.get_object_lock_configuration(Bucket=self._bucket)
            lock = config.get("ObjectLockConfiguration", {})
            if lock.get("ObjectLockEnabled") != "Enabled":
                raise AuditIntegrityError(f"Object Lock is not enabled on {self._bucket}")
            versioning = self._s3.get_bucket_versioning(Bucket=self._bucket)
            if versioning.get("Status") != "Enabled":
                raise AuditIntegrityError(f"versioning is not enabled on {self._bucket}")

        await anyio.to_thread.run_sync(_check)

    async def put_record(self, key: str, body: bytes) -> None:
        """Write one record under Compliance-mode retention, never overwriting."""
        from botocore.exceptions import ClientError

        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": body,
            "ContentType": "application/json",
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(body).digest()).decode(),
            "IfNoneMatch": "*",
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": datetime.now(UTC) + self._retention,
        }
        if self._kms_key_id:
            params["ServerSideEncryption"] = "aws:kms"
            params["SSEKMSKeyId"] = self._kms_key_id

        def _put() -> None:
            try:
                self._s3.put_object(**params)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"PreconditionFailed", "412"}:
                    raise ObjectExistsError(key) from exc
                raise

        await anyio.to_thread.run_sync(_put)

    async def get_record(self, key: str) -> bytes:
        """Fetch a record's bytes."""

        def _get() -> bytes:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
            return body

        return await anyio.to_thread.run_sync(_get)


class AuditLogger:
    """Appends hash-chained records for a tenant.

    Args:
        object_store: WORM store (S3 Object Lock in production).
        head_store: Chain-head lock and persistence.
        key_prefix: Object key prefix for chain records.
        archive_prefix: Object key prefix for archived business records
            (full triage packets, broker reviews).
        clock: Injectable time source for tests.
    """

    def __init__(
        self,
        object_store: AuditObjectStore,
        head_store: ChainHeadStore,
        key_prefix: str = "audit/v1",
        archive_prefix: str = "records/v1",
        clock: Any = None,
    ) -> None:
        self._objects = object_store
        self._heads = head_store
        self._prefix = key_prefix.strip("/")
        self._archive_prefix = archive_prefix.strip("/")
        self._clock = clock or (lambda: datetime.now(UTC))

    async def archive(
        self, *, tenant_id: UUID, kind: str, record_id: UUID, payload: Mapping[str, Any]
    ) -> ArchivedObject:
        """Write a full record (e.g. a triage packet) under Compliance-mode Object Lock.

        The returned SHA-256 must be referenced from a chained audit event so
        the archived object is covered by the hash chain.

        Raises:
            ObjectExistsError: If a record with this id was already archived.
        """
        body = canonical_json(payload)
        key = f"{self._archive_prefix}/tenant={tenant_id}/{kind}/{record_id}.json"
        await self._objects.put_record(key, body)
        return ArchivedObject(key=key, sha256=hashlib.sha256(body).hexdigest())

    def key_for(self, tenant_id: UUID, sequence: int) -> str:
        """Object key for one record; zero-padded so keys sort by sequence."""
        return f"{self._prefix}/tenant={tenant_id}/{sequence:020d}.json"

    async def record(
        self,
        *,
        tenant_id: UUID,
        event_type: AuditEventType,
        actor: str,
        subject_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one event to the tenant's chain and return the stored record.

        Raises:
            AuditIntegrityError: If the record could not be made durable.
        """
        async with self._heads.locked(tenant_id) as head:
            for _attempt in range(3):
                record = self._build(head, event_type, actor, subject_id, details or {})
                key = self.key_for(tenant_id, record["sequence"])
                try:
                    await self._objects.put_record(key, canonical_json(record))
                except ObjectExistsError:
                    # A previous writer stored this sequence in S3 but its DB
                    # commit failed. Adopt the orphan if it extends our head.
                    await self._adopt_orphan(head, key)
                    continue
                head.advance(record["sequence"], record["record_hash"])
                return record
        raise AuditIntegrityError(f"could not append audit record for tenant {tenant_id}")

    async def _adopt_orphan(self, head: ChainHead, key: str) -> None:
        existing = json.loads(await self._objects.get_record(key))
        if (
            existing.get("prev_hash") == head.last_hash
            and existing.get("sequence") == head.sequence + 1
            and compute_record_hash(existing) == existing.get("record_hash")
        ):
            head.advance(existing["sequence"], existing["record_hash"])
            return
        raise AuditIntegrityError(f"conflicting audit object at {key}")

    def _build(
        self,
        head: ChainHead,
        event_type: AuditEventType,
        actor: str,
        subject_id: str | None,
        details: Mapping[str, Any],
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "tenant_id": str(head.tenant_id),
            "sequence": head.sequence + 1,
            "prev_hash": head.last_hash,
            "event_type": event_type.value,
            "occurred_at": self._clock().astimezone(UTC).isoformat(),
            "actor": actor,
            "subject_id": subject_id,
            "details": json.loads(canonical_json(details)),
        }
        record["record_hash"] = compute_record_hash(record)
        return record


class InMemoryChainHeadStore:
    """Process-local head store for tests and local development only."""

    def __init__(self) -> None:
        self._heads: dict[UUID, ChainHead] = {}
        self._locks: dict[UUID, anyio.Lock] = {}

    def locked(self, tenant_id: UUID) -> AbstractAsyncContextManager[ChainHead]:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _cm() -> AsyncIterator[ChainHead]:
            lock = self._locks.setdefault(tenant_id, anyio.Lock())
            async with lock:
                current = self._heads.get(tenant_id) or ChainHead(tenant_id, 0, GENESIS_HASH)
                working = ChainHead(current.tenant_id, current.sequence, current.last_hash)
                yield working
                self._heads[tenant_id] = working

        return _cm()


class InMemoryObjectStore:
    """Write-once dictionary store for tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_record(self, key: str, body: bytes) -> None:
        if key in self.objects:
            raise ObjectExistsError(key)
        self.objects[key] = body

    async def get_record(self, key: str) -> bytes:
        return self.objects[key]
