"""HMAC signing for webhooks exchanged with n8n.

Header format (Stripe-style): `X-Agent-Signature: t=<unix seconds>,v1=<hex>`,
where v1 = HMAC-SHA256(secret, f"{t}.{raw_body}"). The timestamp is inside the
MAC, and receivers reject anything older than the allowed skew, which stops
replays.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

SIGNATURE_HEADER = "X-Agent-Signature"


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify(secret: str, body: bytes, header: str, max_skew_seconds: int, now: float | None = None) -> bool:
    if not secret or not header:
        return False
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        ts = int(parts["t"])
        provided = parts["v1"]
    except (ValueError, KeyError):
        return False
    if abs((now if now is not None else time.time()) - ts) > max_skew_seconds:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)
