"""Tier 2 notifications: signed n8n webhook first, Slack channel as fallback.

Returns True only once a channel has accepted the message. Callers that rely
on delivery (outage incidents, guardrail #2 exemption control) record success
in the database and retry while it's missing.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.services import signing

logger = logging.getLogger(__name__)

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    return _http


async def post_signed(url: str, payload: dict[str, Any]) -> bool:
    secret = get_settings().n8n_outbound_signing_secret.get_secret_value()
    body = signing.canonical_json(payload)
    try:
        resp = await _client().post(
            url,
            content=body,
            headers={"Content-Type": "application/json", signing.SIGNATURE_HEADER: signing.sign(secret, body)},
        )
    except httpx.HTTPError as exc:
        logger.warning("signed webhook failed url_host=%s err=%s", httpx.URL(url).host, type(exc).__name__)
        return False
    if resp.status_code >= 300:
        logger.warning("signed webhook rejected url_host=%s status=%s", httpx.URL(url).host, resp.status_code)
        return False
    return True


async def notify_tier2(
    event: str, tenant_id: str, payload: dict[str, Any], text: str, *, require_n8n: bool = False
) -> bool:
    """Tell technicians about something. `text` is the human-readable fallback.

    require_n8n: only n8n delivery counts (e.g. SOP drafts, whose review buttons
    only n8n renders). The plain Slack fallback is skipped.
    """
    settings = get_settings()
    if settings.n8n_notify_webhook_url and settings.n8n_outbound_signing_secret.get_secret_value():
        envelope = {"event": event, "tenant_id": tenant_id, "data": payload, "text": text}
        if await post_signed(str(settings.n8n_notify_webhook_url), envelope):
            return True
    if require_n8n:
        logger.warning("n8n delivery required but failed event=%s tenant=%s; will retry", event, tenant_id)
        return False

    token = settings.slack_bot_token.get_secret_value()
    if settings.slack_escalation_channel and token:
        try:
            resp = await _client().post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": settings.slack_escalation_channel, "text": text},
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                return True
            logger.warning("slack tier2 notify failed: %s", resp.json().get("error"))
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("slack tier2 notify error: %s", type(exc).__name__)

    logger.error("tier2 notification not delivered event=%s tenant=%s", event, tenant_id)
    return False


async def aclose() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None
