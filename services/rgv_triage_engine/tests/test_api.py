"""HTTP surface: health, disabled docs, webhook authentication and limits."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Any

from fastapi.testclient import TestClient

from conftest import (
    APP_SECRET,
    INBOX,
    RELAY_SECRET,
    WHATSAPP_PHONE_ID,
    FakeEmailAuthenticator,
    FakeExtractor,
    FakeMediaClient,
    FakeRepository,
    make_bol,
)
from triage_engine.schemas.documents import TriageDisposition
from triage_engine.security.signatures import compute_signature
from triage_engine.services.email_intake import RelayRequest, relay_signing_input
from triage_engine.services.whatsapp import DownloadedMedia
from triage_engine.storage.audit import InMemoryObjectStore, verify_chain

# ---------------------------------------------------------------------------
# Health & surface
# ---------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "secure_online", "region": "us-east-1"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "strict-transport-security" in response.headers


def test_public_docs_are_disabled(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert client.get(path).status_code == 404


def test_untrusted_host_rejected(client: TestClient) -> None:
    assert client.get("/v1/health", headers={"host": "evil.example"}).status_code == 400


def test_validation_errors_do_not_echo_input(client: TestClient) -> None:
    response = client.get("/v1/webhooks/whatsapp", params={"hub.mode": "GOMJ800101HTSMRN09"})
    assert response.status_code == 422
    assert "GOMJ" not in response.text


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------


def _whatsapp_payload(message_id: str = "wamid.1", media_id: str = "m-1") -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "waba-1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": WHATSAPP_PHONE_ID},
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": "5218991234567",
                                        "timestamp": "1760000000",
                                        "type": "document",
                                        "document": {
                                            "id": media_id,
                                            "mime_type": "application/pdf",
                                            "filename": "bol.pdf",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()


def _post_whatsapp(client: TestClient, body: bytes, signature: str | None) -> Any:
    headers = {"content-type": "application/json"}
    if signature is not None:
        headers["x-hub-signature-256"] = signature
    return client.post("/v1/webhooks/whatsapp", content=body, headers=headers)


def test_whatsapp_verify_handshake(client: TestClient) -> None:
    params = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"}
    response = client.get("/v1/webhooks/whatsapp", params=params)
    assert response.status_code == 200 and response.text == "12345"
    params["hub.verify_token"] = "wrong"
    assert client.get("/v1/webhooks/whatsapp", params=params).status_code == 403


def test_whatsapp_rejects_missing_bad_and_replayed_signatures(
    client: TestClient, repo: FakeRepository
) -> None:
    body = _whatsapp_payload()
    good = compute_signature(APP_SECRET.encode(), body)
    assert _post_whatsapp(client, body, None).status_code == 401
    assert _post_whatsapp(client, body, "sha256=" + "0" * 64).status_code == 401
    assert _post_whatsapp(client, body, good.replace("sha256=", "sha1=")).status_code == 401
    tampered = body.replace(b"bol.pdf", b"xxx.pdf")
    assert _post_whatsapp(client, tampered, good).status_code == 401
    assert repo.inbound == {}


def test_whatsapp_valid_message_is_triaged(
    client: TestClient,
    repo: FakeRepository,
    extractor: FakeExtractor,
    media_client: FakeMediaClient,
    object_store: InMemoryObjectStore,
    cfdi_xml: bytes,
) -> None:
    # The factura arrives first (as XML), then the BOL as a PDF.
    media_client.media["m-xml"] = DownloadedMedia(cfdi_xml, "application/xml", "a" * 64)
    media_client.media["m-pdf"] = DownloadedMedia(b"%PDF-1.7", "application/pdf", "b" * 64)
    extractor.queue.append(make_bol(driver_license_number="Licencia BCN0012345"))

    for message_id, media_id in (("wamid.x", "m-xml"), ("wamid.p", "m-pdf")):
        body = _whatsapp_payload(message_id, media_id)
        response = _post_whatsapp(client, body, compute_signature(APP_SECRET.encode(), body))
        assert response.status_code == 200, response.text

    assert extractor.calls == 1  # the CFDI never went to the LLM
    assert len(repo.packets) == 1
    packet = repo.packets[0]
    assert packet.disposition is TriageDisposition.CLEAR
    assert packet.bill_of_lading.driver_license_number is not None
    assert "BCN0012345" not in packet.model_dump_json()

    records = [json.loads(object_store.objects[k]) for k in sorted(object_store.objects)]
    verify_chain(records)
    events = [r["event_type"] for r in records]
    assert "llm.extraction" in events and "triage.packet_created" in events
    assert "5218991234567" not in json.dumps(records)

    # Meta redelivers the same message: it is ignored.
    body = _whatsapp_payload("wamid.p", "m-pdf")
    _post_whatsapp(client, body, compute_signature(APP_SECRET.encode(), body))
    assert extractor.calls == 1


# ---------------------------------------------------------------------------
# Body size limit
# ---------------------------------------------------------------------------


def test_declared_oversize_body_rejected(client: TestClient) -> None:
    body = b"x" * (10 * 1024 * 1024 + 1)
    response = client.post("/v1/webhooks/whatsapp", content=body)
    assert response.status_code == 413


def test_streamed_oversize_body_rejected(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(11):
            yield b"x" * (1024 * 1024)

    response = client.post("/v1/webhooks/whatsapp", content=chunks())
    assert response.status_code == 413


def test_body_at_limit_reaches_signature_check(client: TestClient) -> None:
    body = b"x" * (10 * 1024 * 1024)
    assert client.post("/v1/webhooks/whatsapp", content=body).status_code == 401


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------


def _email(attachments: list[tuple[str, str, bytes]]) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Facturacion <facturas@supplier.com.mx>"
    msg["To"] = INBOX
    msg["Subject"] = "Factura A-1001"
    msg["Message-ID"] = "<abc123@supplier.com.mx>"
    msg.set_content("Adjuntamos factura y BOL.")
    for filename, mime, data in attachments:
        maintype, subtype = mime.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def _relay_headers(
    body: bytes, *, secret: str = RELAY_SECRET, ts: int | None = None
) -> dict[str, str]:
    relay = RelayRequest(
        timestamp=str(ts if ts is not None else int(time.time())),
        client_ip="203.0.113.10",
        mail_from="bounce@supplier.com.mx",
        helo="mx.supplier.com.mx",
        rcpt_to=INBOX,
        signature=None,
    )
    return {
        "content-type": "message/rfc822",
        "x-relay-timestamp": relay.timestamp,
        "x-envelope-client-ip": relay.client_ip,
        "x-envelope-mail-from": relay.mail_from,
        "x-envelope-helo": relay.helo,
        "x-envelope-rcpt-to": relay.rcpt_to,
        "x-relay-signature-256": compute_signature(
            secret.encode(), relay_signing_input(relay, body)
        ),
    }


def test_email_requires_valid_fresh_relay_signature(client: TestClient) -> None:
    body = _email([])
    assert (
        client.post(
            "/v1/webhooks/email", content=body, headers=_relay_headers(body, secret="x")
        ).status_code
        == 401
    )
    stale = _relay_headers(body, ts=int(time.time()) - 3600)
    assert client.post("/v1/webhooks/email", content=body, headers=stale).status_code == 401
    swapped = _relay_headers(body)
    swapped["x-envelope-client-ip"] = "198.51.100.7"  # envelope fields are signed too
    assert client.post("/v1/webhooks/email", content=body, headers=swapped).status_code == 401


def test_email_failing_spf_dkim_is_rejected(
    client: TestClient,
    email_authenticator: FakeEmailAuthenticator,
    object_store: InMemoryObjectStore,
) -> None:
    email_authenticator.accept = False
    body = _email([])
    response = client.post("/v1/webhooks/email", content=body, headers=_relay_headers(body))
    assert response.status_code == 403
    records = [json.loads(v) for v in object_store.objects.values()]
    assert records[0]["event_type"] == "email.auth_verdict"
    assert records[0]["details"]["accepted"] is False


def test_email_xxe_attachment_rejected(
    client: TestClient, repo: FakeRepository, object_store: InMemoryObjectStore
) -> None:
    xxe = b'<?xml version="1.0"?><!DOCTYPE c [<!ENTITY x SYSTEM "file:///etc/passwd">]><c>&x;</c>'
    body = _email([("factura.xml", "application/xml", xxe)])
    response = client.post("/v1/webhooks/email", content=body, headers=_relay_headers(body))
    assert response.status_code == 422
    assert "passwd" not in response.text
    assert repo.inbound == {}  # never recorded, so nothing downstream runs
    events = [json.loads(v)["event_type"] for v in object_store.objects.values()]
    assert "webhook.rejected" in events


def test_email_factura_and_bol_produce_packet(
    client: TestClient, repo: FakeRepository, extractor: FakeExtractor, cfdi_xml: bytes
) -> None:
    extractor.queue.append(make_bol(total_gross_weight={"value": "26350", "unit": "LB"}))
    body = _email(
        [
            ("factura.xml", "application/xml", cfdi_xml),
            ("bol.pdf", "application/pdf", b"%PDF-1.7 test"),
            ("malware.exe", "application/octet-stream", b"MZ"),
        ]
    )
    response = client.post("/v1/webhooks/email", content=body, headers=_relay_headers(body))
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "attachments": 2}
    assert len(repo.packets) == 1
    assert repo.packets[0].disposition is TriageDisposition.CLEAR

    again = client.post("/v1/webhooks/email", content=body, headers=_relay_headers(body))
    assert again.json() == {"status": "duplicate"}


def test_failed_audit_write_does_not_swallow_the_retry(
    settings: Any,
    services: Any,
    repo: FakeRepository,
    extractor: FakeExtractor,
    media_client: FakeMediaClient,
) -> None:
    """If the audit store is down, Meta's retry must still be processed."""
    from triage_engine.main import create_app

    media_client.media["m-1"] = DownloadedMedia(b"%PDF-1.7", "application/pdf", "b" * 64)
    extractor.queue.append(make_bol())
    original = services.audit.record
    calls = {"n": 0}

    async def flaky_record(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("S3 unavailable")
        return await original(**kwargs)

    services.audit.record = flaky_record
    body = _whatsapp_payload()
    signature = compute_signature(APP_SECRET.encode(), body)
    with TestClient(create_app(settings, services), raise_server_exceptions=False) as client:
        assert _post_whatsapp(client, body, signature).status_code == 500
        assert repo.inbound == {}
        assert _post_whatsapp(client, body, signature).status_code == 200
    assert extractor.calls == 1
