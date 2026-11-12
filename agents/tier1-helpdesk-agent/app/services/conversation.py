"""Tier 1: multi-turn conversation state and slot-filling.

A conversation is keyed by (tenant_id, thread_id) in Supabase. Each turn, the
model sees the recent history, the facts collected so far (slots), and how
many clarifying questions remain. It may ask at most 2 per thread. That cap is
spent atomically in the database (`increment_clarification` plus a CHECK
constraint), which is the control that keeps clarifying questions exempt from
guardrail #2.

Threads that are escalated, or waiting on a manager, don't go back to the
model. The requester gets a fixed status reply, and their message is kept for
the technician.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Feature, get_settings
from app.schemas.ticket import AgentTriageResult, IncomingTicketWebhook, KnownFact, TriageAction
from app.services import notifier, supabase_client

logger = logging.getLogger(__name__)

MAX_FACTS = 12
OPEN_STATUSES = {"active", "awaiting_user"}
_STATUS_FOR_ACTION = {
    TriageAction.RESOLVE: "active",  # stays open: "that didn't work" continues the thread
    TriageAction.ASK_CLARIFICATION: "awaiting_user",
    TriageAction.AWAIT_MANAGER_APPROVAL: "awaiting_approval",
    TriageAction.ESCALATE_TIER_2: "escalated",
}


@dataclass
class ThreadState:
    tenant_id: str
    thread_id: str
    ticket_id: str
    status: str = "active"
    clarification_count: int = 0
    slots: dict[str, str] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    is_new: bool = True

    @property
    def is_follow_up(self) -> bool:
        return not self.is_new and bool(self.history)

    @property
    def questions_remaining(self) -> int:
        return max(0, get_settings().max_clarifying_questions - self.clarification_count)

    @property
    def first_user_message(self) -> str:
        return next((m["content"] for m in self.history if m["role"] == "user"), "")


def multi_turn_enabled(payload: IncomingTicketWebhook) -> bool:
    return get_settings().feature_enabled(Feature.MULTI_TURN) and bool(payload.thread_id)


def _filters(tenant_id: str, thread_id: str) -> dict[str, str]:
    return {"tenant_id": supabase_client.eq(tenant_id), "thread_id": supabase_client.eq(thread_id)}


async def ensure_thread(payload: IncomingTicketWebhook, thread_id: str | None = None) -> None:
    """Create the thread row if it doesn't exist. Never overwrites counters."""
    settings = get_settings()
    await supabase_client.insert_ignore(
        "conversation_threads",
        {
            "tenant_id": payload.tenant_id,
            "thread_id": thread_id or payload.conversation_key,
            "ticket_id": payload.ticket_id,
            "reporter_email": payload.reporter_email,
            "source_system": payload.source_system,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=settings.thread_ttl_hours)).isoformat(),
        },
        on_conflict="tenant_id,thread_id",
    )


async def load(payload: IncomingTicketWebhook) -> ThreadState:
    """Load (creating if needed) the thread for this ticket, with recent history."""
    settings = get_settings()
    thread_id = payload.conversation_key
    existing = await supabase_client.select(
        "conversation_threads", _filters(payload.tenant_id, thread_id),
        columns="ticket_id,status,clarification_count,slots", limit=1,
    )
    if not existing:
        await ensure_thread(payload)
        return ThreadState(payload.tenant_id, thread_id, payload.ticket_id)

    row = existing[0]
    if row["status"] in ("resolved", "expired"):
        # A new message on a closed thread starts a fresh issue: reset the question budget.
        await supabase_client.update(
            "conversation_threads", _filters(payload.tenant_id, thread_id),
            {"status": "active", "clarification_count": 0, "slots": {}, "last_action": None,
             "expires_at": (datetime.now(timezone.utc) + timedelta(hours=settings.thread_ttl_hours)).isoformat()},
        )
        row = {**row, "status": "active", "clarification_count": 0, "slots": {}}

    history = await supabase_client.select(
        "conversation_messages", {**_filters(payload.tenant_id, thread_id), "order": "created_at.desc"},
        columns="role,content,created_at", limit=settings.max_history_messages,
    )
    return ThreadState(
        tenant_id=payload.tenant_id,
        thread_id=thread_id,
        ticket_id=row["ticket_id"],
        status=row["status"],
        clarification_count=int(row["clarification_count"]),
        slots=dict(row.get("slots") or {}),
        history=list(reversed(history)),
        is_new=False,
    )


def render(state: ThreadState) -> str:
    """Conversation block for the triage prompt (all user-supplied text escaped)."""
    lines = [f"Clarifying questions remaining: {state.questions_remaining}"]
    if state.slots:
        facts = "; ".join(f"{html.escape(k)}={html.escape(v)}" for k, v in sorted(state.slots.items()))
        lines.append(f"Known facts: {facts}")
    for message in state.history:
        role = "Requester" if message["role"] == "user" else "Assistant"
        lines.append(f"{role}: {html.escape(message['content'][:2000])}")
    return "\n".join(lines)


def retrieval_text(state: ThreadState, payload: IncomingTicketWebhook) -> str:
    """Follow-ups like 'Windows 11' retrieve poorly alone: include the original issue."""
    if not state.is_follow_up:
        return ""
    facts = " ".join(f"{k} {v}" for k, v in state.slots.items())
    return f"{state.first_user_message}\n{facts}"


def merge_facts(state: ThreadState, facts: list[KnownFact]) -> dict[str, str]:
    slots = dict(state.slots)
    for fact in facts:
        name = fact.name.strip().lower().replace(" ", "_")[:40]
        if name:
            slots[name] = fact.value.strip()[:200]
    # Keep the most recent MAX_FACTS (dicts preserve insertion order).
    return dict(list(slots.items())[-MAX_FACTS:])


async def spend_clarification(state: ThreadState) -> int | None:
    """Atomically use one clarifying question. None means the cap is reached."""
    count = await supabase_client.rpc(
        "increment_clarification",
        {"p_tenant_id": state.tenant_id, "p_thread_id": state.thread_id,
         "p_max": get_settings().max_clarifying_questions},
    )
    if count is None:
        return None
    state.clarification_count = int(count)
    return state.clarification_count


async def append(state: ThreadState, role: str, content: str, attachment_urls: list[str] | None = None) -> None:
    await supabase_client.insert(
        "conversation_messages",
        {"tenant_id": state.tenant_id, "thread_id": state.thread_id, "role": role,
         "content": content[:20000], "attachment_urls": attachment_urls or []},
    )


async def record_turn(
    state: ThreadState, payload: IncomingTicketWebhook, result: AgentTriageResult, slots: dict[str, str]
) -> None:
    """Persist both sides of the turn and the thread's new status."""
    await append(state, "user", payload.body, [str(u) for u in payload.attachment_urls])
    await append(state, "assistant", result.draft_user_reply)
    status = _STATUS_FOR_ACTION[result.action]
    values: dict[str, Any] = {"slots": slots, "last_action": result.action.value, "status": status}
    if result.action is TriageAction.ESCALATE_TIER_2:
        values["escalation_reason"] = (result.guardrail_overrides or ["agent_escalation"])[0][:200]
    if result.action is TriageAction.ASK_CLARIFICATION:
        values.pop("status")  # increment_clarification already set awaiting_user
    await supabase_client.update("conversation_threads", _filters(state.tenant_id, state.thread_id), values)


async def mark_resolved(tenant_id: str, thread_or_ticket_id: str) -> None:
    await supabase_client.update(
        "conversation_threads",
        {"tenant_id": supabase_client.eq(tenant_id), "ticket_id": supabase_client.eq(thread_or_ticket_id)},
        {"status": "resolved"},
    )


async def set_status(tenant_id: str, thread_id: str, status: str, last_action: TriageAction | None = None,
                     reason: str | None = None) -> None:
    values: dict[str, Any] = {"status": status}
    if last_action is not None:
        values["last_action"] = last_action.value
    if reason:
        values["escalation_reason"] = reason[:200]
    await supabase_client.update("conversation_threads", _filters(tenant_id, thread_id), values)


# ---------------------------------------------------------------------------
# Escalated-thread notifications (guardrail #2 exemption control for approvals)
# ---------------------------------------------------------------------------


async def notify_escalation(tenant_id: str, thread_id: str, ticket_id: str, reason: str,
                            details: str | None = None) -> bool:
    text = f"Tier 2 escalation for {tenant_id}: ticket {ticket_id} (thread {thread_id}). Reason: {reason}."
    if details:
        text += f"\nNotes: {details[:1500]}"
    delivered = await notifier.notify_tier2(
        "thread.escalated", tenant_id,
        {"thread_id": thread_id, "ticket_id": ticket_id, "reason": reason, "notes": details}, text,
    )
    if delivered:
        await supabase_client.update(
            "conversation_threads", _filters(tenant_id, thread_id),
            {"escalation_notified_at": datetime.now(timezone.utc).isoformat()},
        )
    return delivered


async def retry_pending_escalations() -> int:
    """Notify Tier 2 about escalated threads nobody has been told about (e.g. approval expiry)."""
    delivered = 0
    for tenant_id in get_settings().allowed_tenant_ids:
        rows = await supabase_client.select(
            "conversation_threads",
            {"tenant_id": supabase_client.eq(tenant_id), "status": "eq.escalated",
             "escalation_notified_at": "is.null"},
            columns="thread_id,ticket_id,escalation_reason", limit=50,
        )
        for row in rows:
            if await notify_escalation(tenant_id, row["thread_id"], row["ticket_id"],
                                       row.get("escalation_reason") or "escalated"):
                delivered += 1
    return delivered
