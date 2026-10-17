"""Human-in-the-loop broker sign-off, Supabase JWT auth, and region guards."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from uuid import UUID, uuid4

import anyio
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from conftest import (
    JWT_ISSUER,
    JWT_SECRET,
    TENANT,
    FakeRepository,
    make_bol,
    make_factura,
)
from triage_engine.config import Settings
from triage_engine.schemas.documents import CrossBorderLoadTriagePacket, TriageDisposition
from triage_engine.schemas.review import ReviewStatus
from triage_engine.security.auth import SupabaseJWTVerifier, TokenVerificationError
from triage_engine.services.reconciliation import build_triage_packet
from triage_engine.storage.audit import InMemoryObjectStore, canonical_json, verify_chain


def _token(
    sub: UUID = TENANT,
    *,
    aal: str = "aal2",
    secret: str = JWT_SECRET,
    **overrides: Any,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(sub),
        "aud": "authenticated",
        "iss": JWT_ISSUER,
        "role": "authenticated",
        "aal": aal,
        "iat": now,
        "exp": now + 600,
        "session_id": "s-1",
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


def _auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _seed(repo: FakeRepository, **factura_overrides: Any) -> CrossBorderLoadTriagePacket:
    factura, bol = make_factura(**factura_overrides), make_bol()
    packet = build_triage_packet(tenant_id=TENANT, factura=factura, bol=bol)

    async def _save() -> None:
        inbound = uuid4()
        f_id = await repo.save_document(TENANT, inbound, factura, "a" * 64)
        b_id = await repo.save_document(TENANT, inbound, bol, "b" * 64)
        await repo.save_packet(TENANT, packet, f_id, b_id)

    anyio.run(_save)
    return packet


def _chain(store: InMemoryObjectStore) -> list[dict[str, Any]]:
    return [json.loads(store.objects[k]) for k in sorted(store.objects) if k.startswith("audit/")]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": "Basic abc"},
        _auth(_token(secret="x" * 40)),
        _auth(_token(exp=int(time.time()) - 3600)),
        _auth(_token(aud="anon")),
        _auth(_token(iss="https://other.supabase.co/auth/v1")),
        _auth(_token(role="anon")),
        _auth(_token(sub="not-a-uuid")),
        _auth(jwt.encode({"sub": str(TENANT)}, key=None, algorithm="none")),
    ],
)
def test_broker_endpoints_reject_bad_tokens(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/v1/packets", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_asymmetric_jwks_and_algorithm_confusion() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    class _Key:
        key = public_key

    class _JWKClient:
        def get_signing_key_from_jwt(self, token: str) -> _Key:
            return _Key()

    verifier = SupabaseJWTVerifier(issuer=JWT_ISSUER, jwk_client=_JWKClient())
    now = int(time.time())
    claims = {
        "sub": str(TENANT),
        "aud": "authenticated",
        "iss": JWT_ISSUER,
        "role": "authenticated",
        "iat": now,
        "exp": now + 60,
    }
    good = jwt.encode(claims, private_key, algorithm="ES256")
    assert anyio.run(verifier.verify, good).user_id == TENANT

    # HS256 signed with the *public* key must not be accepted.
    pem = public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    forged = _hs256_with_raw_key(claims, pem)
    with pytest.raises(TokenVerificationError):
        anyio.run(verifier.verify, forged)


def _hs256_with_raw_key(claims: dict[str, Any], key: bytes) -> str:
    """Build an HS256 token keyed with arbitrary bytes (PyJWT refuses PEM keys)."""
    import base64
    import hmac

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps(claims).encode())
    signature = hmac.new(key, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{b64(signature)}"


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


def test_queue_and_tenant_isolation(client: TestClient, repo: FakeRepository) -> None:
    packet = _seed(repo)
    rows = client.get("/v1/packets", headers=_auth(_token())).json()
    assert [r["packet_id"] for r in rows] == [str(packet.packet_id)]
    assert rows[0]["review_status"] == "PENDING_BROKER_REVIEW"

    other = _auth(_token(uuid4()))
    assert client.get("/v1/packets", headers=other).json() == []
    assert client.get(f"/v1/packets/{packet.packet_id}", headers=other).status_code == 404
    review = {"decision": "REJECT"}
    assert (
        client.post(
            f"/v1/packets/{packet.packet_id}/reviews", json=review, headers=other
        ).status_code
        == 404
    )

    detail = client.get(f"/v1/packets/{packet.packet_id}", headers=_auth(_token())).json()
    assert detail["review_status"] == "PENDING_BROKER_REVIEW"
    assert len(detail["packet_sha256"]) == 64


# ---------------------------------------------------------------------------
# Sign-off rules
# ---------------------------------------------------------------------------


def test_approving_clear_packet_is_archived_and_chained(
    client: TestClient, repo: FakeRepository, object_store: InMemoryObjectStore
) -> None:
    packet = _seed(repo)
    assert packet.disposition is TriageDisposition.CLEAR
    url = f"/v1/packets/{packet.packet_id}/reviews"
    response = client.post(url, json={"decision": "APPROVE"}, headers=_auth(_token()))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resulting_status"] == "APPROVED_BY_BROKER"
    assert body["reviewer_id"] == str(TENANT)
    assert repo.review_status[packet.packet_id] is ReviewStatus.APPROVED_BY_BROKER

    packet_key = f"records/v1/tenant={TENANT}/triage-packet/{packet.packet_id}.json"
    object_store.objects[packet_key] = canonical_json(packet.model_dump(mode="json"))
    assert body["packet_sha256"] == hashlib.sha256(object_store.objects[packet_key]).hexdigest()
    archive_key = repo.reviews[0][1]
    assert archive_key.startswith(f"records/v1/tenant={TENANT}/broker-review/")
    chain = _chain(object_store)
    verify_chain(chain)
    event = next(r for r in chain if r["event_type"] == "broker.review_recorded")
    assert (
        event["details"]["review_sha256"]
        == hashlib.sha256(object_store.objects[archive_key]).hexdigest()
    )

    # Final: a second decision is refused.
    again = client.post(url, json={"decision": "REJECT"}, headers=_auth(_token()))
    assert again.status_code == 409


def test_approval_requires_mfa_session(
    client: TestClient, repo: FakeRepository, object_store: InMemoryObjectStore
) -> None:
    packet = _seed(repo)
    url = f"/v1/packets/{packet.packet_id}/reviews"
    response = client.post(url, json={"decision": "APPROVE"}, headers=_auth(_token(aal="aal1")))
    assert response.status_code == 403
    assert repo.review_status[packet.packet_id] is ReviewStatus.PENDING_BROKER_REVIEW
    assert "broker.review_denied" in [r["event_type"] for r in _chain(object_store)]
    # Rejecting does not need MFA.
    reject = client.post(url, json={"decision": "REJECT"}, headers=_auth(_token(aal="aal1")))
    assert reject.status_code == 201


def test_hold_requires_acknowledgement_and_written_override(
    client: TestClient, repo: FakeRepository
) -> None:
    packet = _seed(repo, package_count=1180)
    assert packet.disposition is TriageDisposition.HOLD
    url = f"/v1/packets/{packet.packet_id}/reviews"
    headers = _auth(_token())

    no_ack = client.post(url, json={"decision": "APPROVE"}, headers=headers)
    assert no_ack.status_code == 422
    assert "PACKAGE_COUNT_MISMATCH" in no_ack.json()["detail"]

    ack = {"decision": "APPROVE", "acknowledged_findings": ["PACKAGE_COUNT_MISMATCH"]}
    assert client.post(url, json=ack, headers=headers).status_code == 422

    override = {
        **ack,
        "override_reason": "Recount at Pharr dock confirmed 1,200 cartons; CURP GOMJ800101HTSMRN09",
    }
    response = client.post(url, json=override, headers=headers)
    assert response.status_code == 201, response.text
    assert "GOMJ800101HTSMRN09" not in response.json()["override_reason"]
    assert "PII:CURP:" in response.json()["override_reason"]


def test_correction_request_keeps_packet_open(client: TestClient, repo: FakeRepository) -> None:
    packet = _seed(repo)
    url = f"/v1/packets/{packet.packet_id}/reviews"
    headers = _auth(_token())
    first = client.post(
        url, json={"decision": "REQUEST_CORRECTION", "notes": "fix BOL"}, headers=headers
    )
    assert first.json()["resulting_status"] == "CORRECTION_REQUESTED"
    assert client.post(url, json={"decision": "APPROVE"}, headers=headers).status_code == 201


def test_unknown_fields_rejected(client: TestClient, repo: FakeRepository) -> None:
    packet = _seed(repo)
    response = client.post(
        f"/v1/packets/{packet.packet_id}/reviews",
        json={"decision": "APPROVE", "resulting_status": "APPROVED_BY_BROKER"},
        headers=_auth(_token()),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Data residency guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("region", ["eu-west-1", "sa-east-1", "us-gov-west-1", "mx-central-1"])
def test_non_us_regions_rejected(region: str) -> None:
    with pytest.raises(ValueError, match="US region"):
        Settings(aws_region=region)


@pytest.mark.parametrize("model_id", ["global.anthropic.claude-sonnet-5-5", "eu.anthropic.x"])
def test_cross_region_inference_profiles_rejected(model_id: str) -> None:
    with pytest.raises(ValueError, match="in-region"):
        Settings(bedrock_model_id=model_id)


def test_us_inference_profile_allowed() -> None:
    assert Settings(bedrock_model_id="us.anthropic.claude-sonnet-5-5").bedrock_model_id
