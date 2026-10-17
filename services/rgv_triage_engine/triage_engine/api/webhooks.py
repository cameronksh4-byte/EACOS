"""Inbound webhooks: WhatsApp Cloud API and e-mail relay."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError

from triage_engine.container import AppServices
from triage_engine.schemas.webhooks import WhatsAppWebhookPayload
from triage_engine.security.pii import PIIKind
from triage_engine.security.signatures import (
    SignatureError,
    constant_time_equals,
    verify_hmac_sha256,
)
from triage_engine.security.xml_safe import UnsafeXMLError
from triage_engine.services.cfdi import CFDIParseError
from triage_engine.services.email_intake import RelayRequest, parse_email, verify_relay_request
from triage_engine.storage.audit import AuditEventType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _services(request: Request) -> AppServices:
    services: AppServices = request.app.state.services
    return services


async def _run_safely(func: Callable[..., Awaitable[Any]], *args: Any) -> None:
    """Background-task wrapper: never let an exception escape unlogged."""
    try:
        await func(*args)
    except Exception as exc:  # noqa: BLE001 - last-resort guard for background work
        logger.error("background processing failed: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------


@router.get("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_verify(
    request: Request,
    mode: str = Query(alias="hub.mode"),
    verify_token: str = Query(alias="hub.verify_token", max_length=256),
    challenge: str = Query(alias="hub.challenge", max_length=128),
) -> PlainTextResponse:
    """Meta's subscription handshake: echo the challenge if the token matches."""
    expected = _services(request).settings.whatsapp_verify_token.get_secret_value()
    if (
        mode != "subscribe"
        or not expected
        or not constant_time_equals(verify_token, expected)
        or not _CHALLENGE_RE.match(challenge)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return PlainTextResponse(challenge)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, background: BackgroundTasks) -> dict[str, str]:
    """Receive WhatsApp notifications.

    The raw body is authenticated with HMAC-SHA256 (``X-Hub-Signature-256``,
    keyed with the Meta App Secret) before it is parsed. Attachments are
    downloaded and triaged in the background so Meta gets a prompt 200.
    """
    services = _services(request)
    body = await request.body()
    secret = services.settings.whatsapp_app_secret.get_secret_value().encode()
    try:
        verify_hmac_sha256(secret, body, request.headers.get("x-hub-signature-256"))
    except SignatureError:
        logger.warning("whatsapp webhook rejected: bad signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized"
        ) from None

    try:
        payload = WhatsAppWebhookPayload.model_validate_json(body)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid payload"
        ) from None

    payload_sha256 = hashlib.sha256(body).hexdigest()
    for entry in payload.entry:
        for change in entry.changes:
            if change.field != "messages":
                continue
            value = change.value
            tenant_id = await services.repository.resolve_tenant(
                "whatsapp", value.metadata.phone_number_id
            )
            if tenant_id is None:
                logger.warning("whatsapp message for unrouted phone_number_id ignored")
                continue
            masker = services.sanitizer.masker(tenant_id)
            for message in value.messages:
                media = message.media
                if media is None:
                    continue
                # Audit before the idempotency record: if the audit write
                # fails, Meta's retry must not be swallowed as a duplicate.
                await services.audit.record(
                    tenant_id=tenant_id,
                    event_type=AuditEventType.WEBHOOK_RECEIVED,
                    actor="system:whatsapp",
                    subject_id=message.id,
                    details={
                        "channel": "whatsapp",
                        "message_type": message.type,
                        "media_mime_type": media.mime_type,
                        "payload_sha256": payload_sha256,
                        "signature_verified": True,
                    },
                )
                inbound_id = await services.repository.record_inbound(
                    tenant_id,
                    "whatsapp",
                    message.id,
                    payload_sha256,
                    masker.token(PIIKind.PHONE, message.from_),
                )
                if inbound_id is None:
                    continue  # Meta redelivery; already processed
                background.add_task(
                    _run_safely,
                    services.pipeline.process_whatsapp_media,
                    tenant_id,
                    inbound_id,
                    media.id,
                    media.filename,
                )
    return {"status": "accepted"}


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------


@router.post("/email", status_code=status.HTTP_202_ACCEPTED)
async def email_webhook(request: Request, background: BackgroundTasks) -> JSONResponse:
    """Receive a raw RFC 5322 message from the MTA relay.

    Order of checks: relay HMAC (binds envelope to body) -> tenant routing ->
    SPF + aligned DKIM -> MIME parsing -> hardened CFDI XML parsing (XXE
    blocked) -> background extraction of PDFs/images.
    """
    services = _services(request)
    body = await request.body()
    headers = request.headers
    relay = RelayRequest(
        timestamp=headers.get("x-relay-timestamp", ""),
        client_ip=headers.get("x-envelope-client-ip", ""),
        mail_from=headers.get("x-envelope-mail-from", ""),
        helo=headers.get("x-envelope-helo", ""),
        rcpt_to=headers.get("x-envelope-rcpt-to", "").strip().lower(),
        signature=headers.get("x-relay-signature-256"),
    )
    try:
        verify_relay_request(
            services.settings.email_relay_secret.get_secret_value().encode(), relay, body
        )
    except SignatureError:
        logger.warning("email webhook rejected: bad relay signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized"
        ) from None

    tenant_id = await services.repository.resolve_tenant("email", relay.rcpt_to)
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown recipient")

    verdict = await services.email_authenticator.verify(body, relay.envelope)
    await services.audit.record(
        tenant_id=tenant_id,
        event_type=AuditEventType.EMAIL_AUTH_VERDICT,
        actor="system:email",
        details={
            "spf": verdict.spf.value,
            "dkim": verdict.dkim.value,
            "dkim_domain": verdict.dkim_domain,
            "from_domain": verdict.from_domain,
            "aligned": verdict.aligned,
            "accepted": verdict.accepted,
            "payload_sha256": hashlib.sha256(body).hexdigest(),
        },
    )
    if not verdict.accepted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="sender authentication failed"
        )

    parsed = parse_email(body)
    payload_sha256 = hashlib.sha256(body).hexdigest()

    # Vet CFDI XML before recording the message so a hostile file is rejected
    # outright (the rejection itself is audited by the pipeline).
    preparsed = {}
    for attachment in parsed.attachments:
        if not attachment.is_cfdi_xml:
            continue
        try:
            preparsed[attachment.sha256] = await services.pipeline.parse_xml_attachment(
                tenant_id, attachment, "system:email"
            )
        except (UnsafeXMLError, CFDIParseError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="XML attachment rejected",
            ) from None

    # Audit before the idempotency record so a failed audit write leaves the
    # relay's retry free to be processed instead of deduplicated away.
    await services.audit.record(
        tenant_id=tenant_id,
        event_type=AuditEventType.WEBHOOK_RECEIVED,
        actor="system:email",
        subject_id=payload_sha256,
        details={
            "channel": "email",
            "attachments": [
                {"media_type": a.media_type, "sha256": a.sha256} for a in parsed.attachments
            ],
            "skipped_attachments": parsed.skipped_attachments,
        },
    )
    masker = services.sanitizer.masker(tenant_id)
    inbound_id = await services.repository.record_inbound(
        tenant_id,
        "email",
        parsed.message_id,
        payload_sha256,
        masker.token(PIIKind.EMAIL, relay.mail_from),
    )
    if inbound_id is None:
        return JSONResponse({"status": "duplicate"}, status_code=status.HTTP_200_OK)

    background.add_task(
        _run_safely,
        services.pipeline.process_attachments,
        tenant_id,
        inbound_id,
        parsed.attachments,
        "system:email",
        preparsed,
    )
    return JSONResponse(
        {"status": "accepted", "attachments": len(parsed.attachments)},
        status_code=status.HTTP_202_ACCEPTED,
    )
