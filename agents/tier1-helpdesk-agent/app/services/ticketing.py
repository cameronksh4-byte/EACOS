"""Slack, Microsoft Teams and Jira webhook handling.

Each integration does three things: verify the request came from the
platform (HMAC signature), turn it into an IncomingTicketWebhook, and post the
agent's reply back. Escalated tickets also get their internal notes routed to
technicians (a Slack channel, or an internal Jira comment plus a label).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
from collections import OrderedDict
from typing import Any

import httpx

from app.config import get_settings
from app.schemas.ticket import AgentTriageResult, IncomingTicketWebhook
from app.services.pipeline import process_ticket

logger = logging.getLogger(__name__)

_http: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    return _http


async def aclose() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None


class _SeenEvents:
    """Small LRU so platform retries don't create duplicate replies."""

    def __init__(self, size: int = 2048):
        self._items: OrderedDict[str, None] = OrderedDict()
        self._size = size

    def check_and_add(self, key: str) -> bool:
        if key in self._items:
            return True
        self._items[key] = None
        if len(self._items) > self._size:
            self._items.popitem(last=False)
        return False


seen_events = _SeenEvents()


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_slack_signature(
    signing_secret: str, timestamp: str, body: bytes, signature: str, now: float | None = None
) -> bool:
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        if abs((now or time.time()) - int(timestamp)) > 60 * 5:
            return False  # replay protection
    except ValueError:
        return False
    base = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_teams_signature(shared_secret_b64: str, body: bytes, authorization: str) -> bool:
    if not shared_secret_b64 or not authorization.startswith("HMAC "):
        return False
    try:
        key = base64.b64decode(shared_secret_b64)
    except ValueError:
        return False
    expected = base64.b64encode(hmac.new(key, body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, authorization[len("HMAC ") :])


def verify_jira_signature(secret: str, body: bytes, header: str) -> bool:
    if not secret or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

_SLACK_MENTION = re.compile(r"<@[A-Z0-9]+>")


async def _slack_api(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = get_settings().slack_bot_token.get_secret_value()
    resp = await http().post(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        logger.warning("slack %s failed: %s", method, data.get("error"))
    return data


async def _slack_user_email(user_id: str) -> str:
    token = get_settings().slack_bot_token.get_secret_value()
    try:
        resp = await http().get(
            "https://slack.com/api/users.info",
            headers={"Authorization": f"Bearer {token}"},
            params={"user": user_id},
        )
        email = resp.json().get("user", {}).get("profile", {}).get("email")
    except Exception:  # noqa: BLE001
        email = None
    return email or f"{user_id}@slack.invalid"


def slack_event_to_ticket(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Return (channel, thread_ts, text) for events we should answer, else None."""
    if event.get("bot_id") or event.get("subtype"):
        return None  # ignore bots (including ourselves) and edits/joins
    kind = event.get("type")
    if kind == "app_mention" or (kind == "message" and event.get("channel_type") == "im"):
        text = _SLACK_MENTION.sub("", event.get("text", "")).strip()
        if not text:
            return None
        return event["channel"], event.get("thread_ts") or event["ts"], text
    return None


async def handle_slack_event(event: dict[str, Any]) -> None:
    parsed = slack_event_to_ticket(event)
    if parsed is None:
        return
    channel, thread_ts, text = parsed
    settings = get_settings()
    ticket = IncomingTicketWebhook(
        tenant_id=settings.tenant_id,
        ticket_id=f"SLK-{channel}-{thread_ts}",
        reporter_email=await _slack_user_email(event.get("user", "unknown")),
        subject=text.splitlines()[0][:200],
        body=text,
        source_system="slack",
    )
    result = await process_ticket(ticket)
    await _slack_api("chat.postMessage", {"channel": channel, "thread_ts": thread_ts, "text": result.draft_user_reply})
    if result.requires_tier2_escalation and settings.slack_escalation_channel:
        await _slack_api(
            "chat.postMessage",
            {"channel": settings.slack_escalation_channel, "text": escalation_summary(ticket, result)},
        )


# ---------------------------------------------------------------------------
# Microsoft Teams (outgoing webhook)
# ---------------------------------------------------------------------------

_TEAMS_AT = re.compile(r"<at>.*?</at>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")


def teams_activity_to_ticket(activity: dict[str, Any]) -> IncomingTicketWebhook:
    settings = get_settings()
    raw = activity.get("text", "") or ""
    text = _HTML_TAG.sub("", _TEAMS_AT.sub("", raw)).strip()
    sender = activity.get("from", {}) or {}
    reporter = sender.get("aadObjectId") or sender.get("id") or "unknown"
    return IncomingTicketWebhook(
        tenant_id=settings.tenant_id,
        ticket_id=f"TMS-{activity.get('id', int(time.time()))}",
        reporter_email=f"{reporter}@teams.invalid",
        subject=(text.splitlines()[0] if text else "(empty message)")[:200],
        body=text or "(empty message)",
        source_system="teams",
    )


async def post_teams_followup(text: str) -> None:
    url = get_settings().teams_incoming_webhook_url.get_secret_value()
    if not url:
        logger.warning("teams follow-up dropped: TEAMS_INCOMING_WEBHOOK_URL not set")
        return
    resp = await http().post(url, json={"text": text})
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


def _adf_text(node: Any) -> str:
    """Flatten Atlassian Document Format (or a plain string) into text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        inner = "".join(_adf_text(c) for c in node.get("content", []) or [])
        return inner + ("\n" if node.get("type") in {"paragraph", "heading", "listItem"} else "")
    if isinstance(node, list):
        return "".join(_adf_text(n) for n in node)
    return ""


def _adf_doc(text: str) -> dict[str, Any]:
    paragraphs = [p for p in text.split("\n") if p.strip()]
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs],
    }


def jira_issue_to_ticket(issue: dict[str, Any]) -> IncomingTicketWebhook:
    settings = get_settings()
    fields = issue.get("fields", {}) or {}
    reporter = (fields.get("reporter") or {}).get("emailAddress") or "unknown@jira.invalid"
    return IncomingTicketWebhook(
        tenant_id=settings.tenant_id,
        ticket_id=issue["key"],
        reporter_email=reporter,
        subject=(fields.get("summary") or "(no summary)")[:1000],
        body=_adf_text(fields.get("description")).strip() or "(no description)",
        source_system="jira",
    )


async def _jira(method: str, path: str, payload: dict[str, Any]) -> None:
    settings = get_settings()
    resp = await http().request(
        method,
        f"{settings.jira_base_url.rstrip('/')}{path}",
        auth=(settings.jira_email, settings.jira_api_token.get_secret_value()),
        json=payload,
    )
    if resp.status_code >= 400:
        logger.warning("jira %s %s failed status=%s", method, path, resp.status_code)


async def handle_jira_issue(issue: dict[str, Any]) -> None:
    settings = get_settings()
    ticket = jira_issue_to_ticket(issue)
    result = await process_ticket(ticket)
    key = ticket.ticket_id
    await _jira("POST", f"/rest/api/3/issue/{key}/comment", {"body": _adf_doc(result.draft_user_reply)})
    if result.requires_tier2_escalation:
        await _jira(
            "POST",
            f"/rest/api/3/issue/{key}/comment",
            {
                "body": _adf_doc(escalation_summary(ticket, result)),
                # Jira Service Management: keep technician notes internal.
                "properties": [{"key": "sd.public.comment", "value": {"internal": True}}],
            },
        )
        await _jira(
            "PUT",
            f"/rest/api/3/issue/{key}",
            {"update": {"labels": [{"add": settings.jira_escalation_label}]}},
        )


# ---------------------------------------------------------------------------


def escalation_summary(ticket: IncomingTicketWebhook, result: AgentTriageResult) -> str:
    return (
        f"Tier 2 escalation: {ticket.ticket_id} ({ticket.source_system})\n"
        f"Reporter: {ticket.reporter_email}\n"
        f"Scope: {result.scope.value} | Category: {result.category.value} | "
        f"Severity: {result.severity.value} | Confidence: {result.confidence_score:.2f}\n"
        f"Subject: {ticket.subject}\n"
        f"Cited SOPs: {', '.join(result.cited_sop_ids) or 'none'}\n"
        f"Notes: {result.internal_tech_notes or '-'}"
    )
