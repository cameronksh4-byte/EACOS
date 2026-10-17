"""Constant-time webhook signature verification."""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_PREFIX = "sha256="


class SignatureError(Exception):
    """The request signature is missing, malformed, or does not match."""


def compute_signature(secret: bytes, body: bytes) -> str:
    """Return ``sha256=<hex>`` HMAC-SHA256 of ``body`` (Meta's header format)."""
    return SIGNATURE_PREFIX + hmac.new(secret, body, hashlib.sha256).hexdigest()


def verify_hmac_sha256(secret: bytes, body: bytes, header_value: str | None) -> None:
    """Verify an ``X-Hub-Signature-256`` style header against the raw body.

    The comparison uses :func:`hmac.compare_digest` so its duration does not
    depend on how many leading characters match, which defeats timing
    attacks that recover a valid signature byte by byte.

    Args:
        secret: Shared secret (for WhatsApp, the Meta *App Secret*).
        body: The exact raw request bytes. Never a re-serialised JSON body.
        header_value: The received header value, e.g. ``sha256=ab12...``.

    Raises:
        SignatureError: If verification fails for any reason.
    """
    if not secret:
        raise SignatureError("signing secret not configured")
    if not header_value or not header_value.startswith(SIGNATURE_PREFIX):
        raise SignatureError("missing or malformed signature header")
    received = header_value.strip().lower().encode("ascii", errors="replace")
    expected = compute_signature(secret, body).encode("ascii")
    if not hmac.compare_digest(expected, received):
        raise SignatureError("signature mismatch")


def constant_time_equals(a: str, b: str) -> bool:
    """Compare two secrets (e.g. verify tokens) in constant time."""
    return hmac.compare_digest(a.encode(), b.encode())
