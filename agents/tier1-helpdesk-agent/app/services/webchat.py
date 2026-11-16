"""Public website chat: a metered free trial of the help desk.

    visitor (browser) -> company website's server -> POST /v1/webchat/message (signed)

The browser never calls this API. The website's server identifies the visitor
(a cookie it sets), passes the visitor's network address, and, once the person
has signed up and verified their address, their email. Every request is HMAC-
signed with WEBCHAT_SIGNING_SECRET.

Metering happens in the database, atomically, before any model call:

    anonymous visitor   WEBCHAT_VISITOR_FREE_USES questions in total (default 5),
                        and a per-network daily cap so clearing cookies doesn't
                        reset the trial. After that: sign up.
    signed-up account   a monthly allowance by plan (free, starter, ...), then
                        purchased credits. After that: buy credits or upgrade.

The answer comes from the normal pipeline in sandbox mode: grounded in the
website tenant's SOPs, same guardrails, but no side effects (nobody is paged,
nothing is provisioned). The whole chat spends against its own hard monthly
model budget. If no answer could be produced, the question is given back.

Only plain reply text and counters leave this module. Internal notes never do.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import Settings, get_settings
from app.schemas.ticket import (
    AgentTriageResult,
    IncomingTicketWebhook,
    ScopeVerdict,
    TicketCategory,
    TriageAction,
    WebChatAccount,
    WebChatGrant,
    WebChatRequest,
    WebChatResponse,
)
from app.services import guardrails, supabase_client
from app.services.pipeline import process_ticket

logger = logging.getLogger(__name__)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

DEMO_NOTE = (
    "This website chat is a demonstration and isn't connected to a technician, "
    "so nobody will follow up on this one."
)


class InvalidRequest(ValueError):
    """The website's server sent something unusable (answered with 422, nothing is counted)."""


def hash_ip(address: str) -> str:
    """Keyed hash of the visitor's network (IPv6: the /64), so no address is stored."""
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError as exc:
        raise InvalidRequest("client_ip is not an IP address") from exc
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            network = str(ip.ipv4_mapped)
        else:
            network = str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address)
    else:
        network = str(ip)
    key = get_settings().webchat_signing_secret.get_secret_value().encode()
    return hmac.new(key, f"webchat-ip:{network}".encode(), hashlib.sha256).hexdigest()


def clean_message(text: str, settings: Settings) -> str:
    text = _CONTROL.sub("", text).strip()
    if len(text) < 2:
        raise InvalidRequest("message is empty")
    if len(text) > settings.webchat_max_message_chars:
        raise InvalidRequest(f"message is longer than {settings.webchat_max_message_chars} characters")
    return text


def _counters(row: dict[str, Any]) -> dict[str, Any]:
    used, allowance = int(row.get("used") or 0), int(row.get("allowance") or 0)
    return {"plan": row.get("plan"), "used": used, "allowance": allowance,
            "remaining": max(0, allowance - used), "credits": int(row.get("credits") or 0)}


def signup_required(settings: Settings, row: dict[str, Any]) -> WebChatResponse:
    return WebChatResponse(
        status="signup_required",
        reply=(
            f"You've used your {settings.webchat_visitor_free_uses} free questions. "
            "Create a free account to keep going. It takes a minute, and the free plan includes "
            f"{settings.webchat_plan_monthly_limits.get('free', 0)} questions a month."
        ),
        signup_url=settings.webchat_signup_url or None,
        **_counters(row),
    )


def limit_reached(settings: Settings, row: dict[str, Any]) -> WebChatResponse:
    return WebChatResponse(
        status="limit_reached",
        signed_in=True,
        reply=(
            "You've used all the questions included in your plan this month. "
            "You can add more questions with a credit pack, or move up a plan for a larger monthly allowance."
        ),
        upgrade_url=settings.webchat_upgrade_url or None,
        **_counters(row),
    )


def unavailable(row: dict[str, Any] | None = None, *, signed_in: bool = False) -> WebChatResponse:
    return WebChatResponse(
        status="unavailable",
        signed_in=signed_in,
        reply="Sorry, the help desk assistant isn't available right now. Please try again in a little while. "
              "This question wasn't counted.",
        **_counters(row or {}),
    )


def public_reply(result: AgentTriageResult) -> tuple[str, str, list[str]]:
    """(outcome, reply text, cited SOP ids) for a website visitor.

    The pipeline's handoff templates promise a technician will follow up. No
    technician is behind the website chat, so escalations say what a live
    deployment would do instead of making that promise.
    """
    if result.scope is ScopeVerdict.NOT_IT_SUPPORT:
        return "declined", result.draft_user_reply, []  # the fixed courteous decline
    if result.action is TriageAction.RESOLVE and not result.requires_tier2_escalation and result.cited_sop_ids:
        return "resolved", result.draft_user_reply, list(result.cited_sop_ids)
    if result.category is TicketCategory.SECURITY_INCIDENT:
        text = (
            "Thanks for flagging this. It sounds like a possible security issue, and those always go "
            "straight to a person.\n\n"
            "1. Don't click any more links or open attachments in the suspicious message.\n"
            "2. Don't delete the message.\n"
            "3. If you typed a password anywhere suspicious, change it from a device you trust. "
            "Never send a password in a chat.\n\n"
            "In a live deployment I would alert your security or IT team right now with a summary. "
            + DEMO_NOTE
        )
    elif result.scope is ScopeVerdict.BEYOND_TIER1:
        text = (
            "This one needs administrator access or specialist work, which is beyond what a Tier 1 help desk "
            "does. In a live deployment I would hand it to your IT technician with my notes, and tell you "
            "it's with them. " + DEMO_NOTE
        )
    else:
        text = (
            "I don't have a documented fix for this in the knowledge base I'm using here, and I won't guess. "
            "In a live deployment I would pass it to your IT technician with a summary of what you told me. "
            + DEMO_NOTE
        )
    return "escalated", text, []


async def handle_message(request: WebChatRequest) -> WebChatResponse:
    settings = get_settings()
    message = clean_message(request.message, settings)
    ip_hash = hash_ip(request.client_ip)
    signed_in = request.account_email is not None
    meter = {"p_visitor_id": request.visitor_id, "p_ip_hash": ip_hash, "p_account_email": request.account_email}

    try:
        rows = await supabase_client.rpc("webchat_consume", {
            **meter,
            "p_visitor_limit": settings.webchat_visitor_free_uses,
            "p_ip_daily_limit": settings.webchat_ip_daily_free_uses,
            "p_plan_limits": settings.webchat_plan_monthly_limits,
        })
        row = rows[0] if isinstance(rows, list) else rows
        if not isinstance(row, dict):
            raise ValueError("unexpected metering response")
    except Exception as exc:  # noqa: BLE001  (fail closed: no count means no answer)
        logger.error("webchat metering unavailable err=%s", type(exc).__name__)
        return unavailable(signed_in=signed_in)

    if not row.get("allowed"):
        if row.get("reason") == "plan_limit":
            return limit_reached(settings, row)
        return signup_required(settings, row)

    payload = IncomingTicketWebhook(
        tenant_id=settings.webchat_tenant_id,
        ticket_id=f"WEB-{uuid.uuid4().hex[:16]}",
        reporter_email=request.account_email or f"{request.visitor_id[:48]}@webchat.invalid",
        subject=message.splitlines()[0][:200],
        body=message,
        source_system="web",
    )
    result = await process_ticket(payload, sandbox=True)

    if guardrails.TRIAGE_UNAVAILABLE in result.guardrail_overrides:
        # No answer was produced (model down, budget cap, error): give the question back.
        try:
            await supabase_client.rpc("webchat_refund", {**meter, "p_charged": row.get("charged")})
        except Exception as exc:  # noqa: BLE001
            logger.warning("webchat refund failed err=%s", type(exc).__name__)
        counters = dict(row)
        if row.get("charged") in ("trial", "allowance"):
            counters["used"] = max(0, int(row.get("used") or 0) - 1)
        elif row.get("charged") == "credit":
            counters["credits"] = int(row.get("credits") or 0) + 1
        return unavailable(counters, signed_in=signed_in)

    outcome, reply, cited = public_reply(result)
    logger.info("webchat answered ticket=%s outcome=%s signed_in=%s charged=%s",
                payload.ticket_id, outcome, signed_in, row.get("charged"))
    return WebChatResponse(status="answered", outcome=outcome, reply=reply, cited_sop_ids=cited,  # type: ignore[arg-type]
                           signed_in=signed_in, **_counters(row))


async def apply_grant(grant: WebChatGrant) -> dict[str, Any]:
    """Plan change and/or credit purchase from the website's payment handler. Idempotent per grant_id."""
    settings = get_settings()
    if grant.plan is not None and grant.plan not in settings.webchat_plan_monthly_limits:
        raise InvalidRequest(f"unknown plan {grant.plan!r}")
    rows = await supabase_client.rpc("webchat_apply_grant", {
        "p_grant_id": grant.grant_id, "p_email": grant.email, "p_plan": grant.plan,
        "p_add_credits": grant.add_credits,
    })
    row = rows[0] if isinstance(rows, list) else rows
    logger.info("webchat grant id=%s applied=%s plan=%s credits_added=%d",
                grant.grant_id[:40], row.get("applied"), grant.plan, grant.add_credits)
    return {"applied": bool(row.get("applied")), "plan": row.get("plan"), "credits": int(row.get("credits") or 0)}


async def account(email: str) -> WebChatAccount | None:
    settings = get_settings()
    rows = await supabase_client.select(
        "webchat_accounts", {"email": supabase_client.eq(email)},
        columns="email,plan,credits,period_start,period_uses", limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    month = datetime.now(timezone.utc).strftime("%Y-%m-01")
    used = int(row.get("period_uses") or 0) if str(row.get("period_start")) >= month else 0
    return WebChatAccount(email=row["email"], plan=row["plan"], used=used,
                          allowance=int(settings.webchat_plan_monthly_limits.get(row["plan"], 0)),
                          credits=int(row.get("credits") or 0))
