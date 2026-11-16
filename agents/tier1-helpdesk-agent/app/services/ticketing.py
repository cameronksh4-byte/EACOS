"""Slack, Microsoft Teams and Jira webhook handling.

Each integration does three things: verify the request came from the
platform (HMAC signature), turn it into an IncomingTicketWebhook, and post the
agent's reply back. Escalated tickets also get their internal notes routed to
technicians (a Slack channel, or an internal Jira comment plus a label).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import logging
import re
import time
from collections import OrderedDict
from typing import Any

import httpx

from app.config import Feature, get_settings
from app.schemas.ticket import AgentTriageResult, IncomingTicketWebhook
from app.services import context_enrichment, supabase_client
from app.services.pipeline import escalation_summary, process_ticket


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
    except (ValueError, OverflowError):
        return False
    base = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    # Compare bytes: compare_digest raises on non-ASCII str input.
    return hmac.compare_digest(expected.encode(), signature.encode())


def verify_teams_signature(shared_secret_b64: str, body: bytes, authorization: str) -> bool:
    if not shared_secret_b64 or not authorization.startswith("HMAC "):
        return False
    try:
        key = base64.b64decode(shared_secret_b64)
    except (ValueError, binascii.Error):
        return False
    expected = base64.b64encode(hmac.new(key, body, hashlib.sha256).digest())
    return hmac.compare_digest(expected, authorization[len("HMAC ") :].encode())


def verify_jira_signature(secret: str, body: bytes, header: str) -> bool:
    if not secret or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.encode())


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

_SLACK_MENTION = re.compile(r"<@[A-Z0-9]+>")
# Message subtypes that are still a person talking (everything else is an edit, join, bot...).
_SLACK_USER_SUBTYPES = (None, "file_share", "thread_broadcast")


def slack_escape(text: str) -> str:
    """Slack control characters, so requester or model text can't ping <!channel> or forge links."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


def image_urls(items: Any, *, url_key: str, type_key: str, limit: int = 10) -> list[str]:
    """https image URLs from a platform's attachment list (vision fetches them safely)."""
    urls: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url, kind = item.get(url_key), str(item.get(type_key) or "")
        if isinstance(url, str) and url.startswith("https://") and kind.startswith("image/"):
            urls.append(url)
    return urls[:limit]


def slack_event_to_ticket(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Return (channel, thread_ts, text) for events we should answer, else None."""
    if event.get("bot_id") or event.get("subtype") not in _SLACK_USER_SUBTYPES:
        return None  # ignore bots (including ourselves) and edits/joins
    kind = event.get("type")
    if kind == "app_mention" or (kind == "message" and event.get("channel_type") == "im"):
        text = _SLACK_MENTION.sub("", event.get("text", "")).strip()
        if not text:
            return None
        return event["channel"], event.get("thread_ts") or event["ts"], text
    return None


def slack_thread_reply(event: dict[str, Any], bot_user_id: str = "") -> tuple[str, str, str] | None:
    """A plain reply inside a channel thread (no @mention), e.g. answering a clarifying question.

    A reply that @mentions the bot also arrives as an `app_mention` event, which
    is the one that gets answered: handling both would process the message twice.
    """
    if (event.get("bot_id") or event.get("subtype") not in _SLACK_USER_SUBTYPES
            or event.get("type") != "message"):
        return None
    if bot_user_id and f"<@{bot_user_id}>" in event.get("text", ""):
        return None
    thread_ts, ts = event.get("thread_ts"), event.get("ts")
    text = _SLACK_MENTION.sub("", event.get("text", "")).strip()
    if not thread_ts or thread_ts == ts or not text or event.get("channel_type") == "im":
        return None
    return event["channel"], thread_ts, text


async def _known_thread(thread_id: str) -> bool:
    rows = await supabase_client.select(
        "conversation_threads",
        {"tenant_id": supabase_client.eq(get_settings().tenant_id), "thread_id": supabase_client.eq(thread_id)},
        columns="thread_id", limit=1,
    )
    return bool(rows)


async def handle_slack_event(event: dict[str, Any], bot_user_id: str = "") -> None:
    parsed = slack_event_to_ticket(event)
    if parsed is None and get_settings().feature_enabled(Feature.MULTI_TURN):
        reply = slack_thread_reply(event, bot_user_id)
        if reply and await _known_thread(f"slack:{reply[0]}:{reply[1]}"):
            parsed = reply
    if parsed is None:
        return
    channel, thread_ts, text = parsed
    # One Slack message is one turn, however many event types announce it.
    if event.get("ts") and seen_events.check_and_add(f"slack-msg:{channel}:{event['ts']}"):
        return
    settings = get_settings()
    ticket = IncomingTicketWebhook(
        tenant_id=settings.tenant_id,
        ticket_id=f"SLK-{channel}-{thread_ts}",
        thread_id=f"slack:{channel}:{thread_ts}",
        reporter_email=await _slack_user_email(event.get("user", "unknown")),
        subject=text.splitlines()[0][:200],
        body=text,
        source_system="slack",
        attachment_urls=image_urls(event.get("files"), url_key="url_private", type_key="mimetype"),
    )
    result = await process_ticket(ticket)
    # Tier 2 is notified by the pipeline (every channel, with retry where a thread exists).
    await _slack_api("chat.postMessage",
                     {"channel": channel, "thread_ts": thread_ts, "text": slack_escape(result.draft_user_reply)})


# ---------------------------------------------------------------------------
# Microsoft Teams (outgoing webhook)
# ---------------------------------------------------------------------------

_TEAMS_AT = re.compile(r"<at>.*?</at>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")


async def process_teams_activity(activity: dict[str, Any]) -> AgentTriageResult:
    """Resolve the sender's real address (so identity context and approvals work), then triage."""
    sender = activity.get("from") if isinstance(activity.get("from"), dict) else {}
    email = await context_enrichment.resolve_teams_email(str(sender.get("aadObjectId") or ""))
    return await process_ticket(teams_activity_to_ticket(activity, email))


def teams_activity_to_ticket(activity: dict[str, Any], reporter_email: str | None = None) -> IncomingTicketWebhook:
    settings = get_settings()
    raw = activity.get("text", "") or ""
    if not isinstance(raw, str):
        raw = ""
    # Teams sends HTML: drop the tags, then turn &amp; / &lt; / &nbsp; back into characters.
    text = html.unescape(_HTML_TAG.sub("", _TEAMS_AT.sub("", raw))).replace("\xa0", " ").strip()
    sender = activity.get("from") if isinstance(activity.get("from"), dict) else {}
    reporter = str(sender.get("aadObjectId") or sender.get("id") or "unknown")[:200]
    return IncomingTicketWebhook(
        tenant_id=settings.tenant_id,
        ticket_id=f"TMS-{str(activity.get('id') or int(time.time()))[:120]}",
        reporter_email=reporter_email or f"{reporter}@teams.invalid",
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
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    reporter_field = fields.get("reporter") if isinstance(fields.get("reporter"), dict) else {}
    reporter = reporter_field.get("emailAddress") or "unknown@jira.invalid"
    return IncomingTicketWebhook(
        tenant_id=settings.tenant_id,
        ticket_id=issue["key"],
        reporter_email=reporter,
        subject=(fields.get("summary") or "(no summary)")[:1000],
        body=_adf_text(fields.get("description")).strip() or "(no description)",
        source_system="jira",
        attachment_urls=image_urls(fields.get("attachment"), url_key="content", type_key="mimeType"),
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
