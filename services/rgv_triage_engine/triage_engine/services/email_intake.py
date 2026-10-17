"""Inbound e-mail: relay authentication and MIME attachment extraction."""

from __future__ import annotations

import email
import email.policy
import hashlib
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses

from triage_engine.security.email_auth import EnvelopeInfo
from triage_engine.security.signatures import verify_hmac_sha256

MAX_ATTACHMENTS = 20
RELAY_MAX_CLOCK_SKEW_SECONDS = 300
XML_TYPES = frozenset({"application/xml", "text/xml"})
EXTRACTABLE_TYPES = frozenset(
    {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif"}
)


@dataclass(frozen=True, slots=True)
class Attachment:
    """One attachment pulled from a message or a WhatsApp media download."""

    filename: str
    media_type: str
    content: bytes
    sha256: str

    @property
    def is_cfdi_xml(self) -> bool:
        """True for XML files, which are parsed as CFDI without the LLM."""
        return self.media_type in XML_TYPES or self.filename.lower().endswith(".xml")


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    """The parts of an inbound e-mail the pipeline uses."""

    message_id: str
    recipients: list[str]
    attachments: list[Attachment] = field(default_factory=list)
    skipped_attachments: int = 0


@dataclass(frozen=True, slots=True)
class RelayRequest:
    """Envelope data and signature headers sent by the MTA relay."""

    timestamp: str
    client_ip: str
    mail_from: str
    helo: str
    rcpt_to: str
    signature: str | None

    @property
    def envelope(self) -> EnvelopeInfo:
        """SMTP envelope for SPF evaluation."""
        return EnvelopeInfo(client_ip=self.client_ip, mail_from=self.mail_from, helo=self.helo)


def verify_relay_request(secret: bytes, relay: RelayRequest, body: bytes) -> None:
    """Authenticate the relay and bind the envelope headers to the body.

    The signature covers the timestamp and every envelope field, not just
    the body. Otherwise an attacker replaying a captured request could swap
    in a client IP that passes SPF for a spoofed sender.

    Raises:
        triage_engine.security.signatures.SignatureError: Bad or stale signature.
    """
    from triage_engine.security.signatures import SignatureError

    try:
        sent_at = int(relay.timestamp)
    except ValueError as exc:
        raise SignatureError("invalid relay timestamp") from exc
    if abs(time.time() - sent_at) > RELAY_MAX_CLOCK_SKEW_SECONDS:
        raise SignatureError("stale relay request")
    signed = relay_signing_input(relay, body)
    verify_hmac_sha256(secret, signed, relay.signature)


def relay_signing_input(relay: RelayRequest, body: bytes) -> bytes:
    """Canonical byte string the relay signs."""
    header = "\n".join(
        [relay.timestamp, relay.client_ip, relay.mail_from, relay.helo, relay.rcpt_to]
    )
    return header.encode("utf-8") + b"\n" + body


def parse_email(raw: bytes) -> ParsedEmail:
    """Parse a raw RFC 5322 message and collect processable attachments.

    Only CFDI XML, PDFs and common image types are kept. Everything else
    (executables, archives, office macros) is counted and dropped unread.
    """
    message = email.message_from_bytes(raw, policy=email.policy.default)
    if not isinstance(message, EmailMessage):
        raise TypeError("expected an EmailMessage under the default policy")
    message_id = (message.get("Message-ID") or "").strip() or (
        "sha256:" + hashlib.sha256(raw).hexdigest()
    )
    recipients = [
        addr.lower()
        for _, addr in getaddresses(
            [str(v) for v in (message.get_all("To") or []) + (message.get_all("Cc") or [])]
        )
        if addr
    ]

    attachments: list[Attachment] = []
    skipped = 0
    for part in message.iter_attachments():
        if len(attachments) >= MAX_ATTACHMENTS:
            skipped += 1
            continue
        media_type = part.get_content_type().lower()
        filename = part.get_filename() or "attachment"
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            skipped += 1
            continue
        if filename.lower().endswith(".xml") and media_type == "application/octet-stream":
            media_type = "application/xml"
        if media_type not in XML_TYPES | EXTRACTABLE_TYPES:
            skipped += 1
            continue
        attachments.append(
            Attachment(
                filename=filename[:255],
                media_type=media_type,
                content=payload,
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    return ParsedEmail(
        message_id=message_id[:255],
        recipients=recipients,
        attachments=attachments,
        skipped_attachments=skipped,
    )
