"""Deterministic policy applied after the model answers.

The model proposes; this module decides. In particular:

* Out-of-scope requests always get a fixed, courteous template, never
  model-written text. Tone stays consistent, and no partial "help" with an
  out-of-scope request leaks through.
* Cited SOP IDs must be ones we actually retrieved (no invented citations).
* Security incidents, low confidence, and uncited answers always escalate.
* When a ticket escalates, the requester gets a handoff note. An unverified
  draft is passed to the technician, never sent to the user.
"""

from __future__ import annotations

from app.config import Settings
from app.schemas.ticket import (
    AgentTriageResult,
    IncomingTicketWebhook,
    LLMTriageOutput,
    ScopeVerdict,
    SeverityLevel,
    TicketCategory,
)

TIER1_SERVICES = (
    "passwords and sign-in, VPN and Wi-Fi, printers and other hardware, "
    "email and Office apps, and access to approved software"
)


def decline_not_it(settings: Settings) -> str:
    return (
        "Hi there, and thanks for reaching out!\n\n"
        f"I'm the {settings.helpdesk_name} assistant, and I'm only able to help with everyday IT "
        f"support, like {TIER1_SERVICES}. This request is outside what I can help with, so I'm not "
        "able to take it on. I'm sorry I can't be more help with this one.\n\n"
        "If it's an HR, payroll, facilities, or other business question, your manager or the "
        "relevant department will be the best place to ask.\n\n"
        "And if anything IT-related comes up, I'm always happy to help!"
    )


def decline_beyond_tier1(settings: Settings, ticket_id: str) -> str:
    return (
        "Hi there, and thanks for the details!\n\n"
        "This request needs access and expertise beyond what the Tier 1 help desk can provide, "
        "so I'm not able to complete it myself. I've passed it, along with your notes, to "
        f"{settings.tier2_contact_hint}, who will follow up with you.\n\n"
        f"Your reference number is {ticket_id}. Thank you for your patience!"
    )


def handoff_message(settings: Settings, ticket_id: str) -> str:
    return (
        "Hi there, and thanks for reaching out!\n\n"
        "I want to make sure you get the right fix, so I've passed your ticket to "
        f"{settings.tier2_contact_hint}, who will follow up with you shortly.\n\n"
        f"Your reference number is {ticket_id}. Thank you for your patience!"
    )


def security_message(settings: Settings, ticket_id: str) -> str:
    return (
        "Hi there, and thank you for reporting this quickly. That really helps keep everyone safe.\n\n"
        "While our security team takes a look:\n"
        "1. Please don't click any more links or open attachments in the suspicious message.\n"
        "2. Don't delete the message. The team may need it.\n"
        "3. If you entered your password anywhere suspicious, tell us in a reply. "
        "Please don't send the password itself.\n\n"
        f"Your report has been escalated as a priority. Reference: {ticket_id}."
    )


def unavailable_message(settings: Settings, ticket_id: str) -> str:
    return (
        "Hi there, and thanks for reaching out!\n\n"
        "I wasn't able to look at your request automatically just now, so I've passed it to "
        f"{settings.tier2_contact_hint}, who will follow up with you.\n\n"
        f"Your reference number is {ticket_id}. Thank you for your patience!"
    )


def _notes(*parts: str | None) -> str | None:
    text = "\n".join(p.strip() for p in parts if p and p.strip())
    return text or None


def enforce_policy(
    payload: IncomingTicketWebhook,
    draft: LLMTriageOutput,
    retrieved_sop_ids: set[str],
    settings: Settings,
    model_used: str,
) -> AgentTriageResult:
    notes_prefix = f"[triaged by {model_used}]"

    # 1. Out of scope: not IT support. Courteous decline, nothing else.
    if draft.scope is ScopeVerdict.NOT_IT_SUPPORT:
        return AgentTriageResult(
            scope=ScopeVerdict.NOT_IT_SUPPORT,
            category=TicketCategory.UNKNOWN,
            severity=SeverityLevel.LOW,
            confidence_score=draft.confidence_score,
            requires_tier2_escalation=False,
            cited_sop_ids=[],
            draft_user_reply=decline_not_it(settings),
            internal_tech_notes=_notes(
                notes_prefix, "Declined: not an IT support request.", draft.internal_tech_notes
            ),
        )

    # 2. IT, but beyond Tier 1: politely decline to act and hand to Tier 2.
    if draft.scope is ScopeVerdict.BEYOND_TIER1:
        return AgentTriageResult(
            scope=ScopeVerdict.BEYOND_TIER1,
            category=draft.category,
            severity=draft.severity,
            confidence_score=draft.confidence_score,
            requires_tier2_escalation=True,
            cited_sop_ids=[],
            draft_user_reply=decline_beyond_tier1(settings, payload.ticket_id),
            internal_tech_notes=_notes(
                notes_prefix, "Beyond Tier 1 scope; escalated.", draft.internal_tech_notes
            ),
        )

    # 3. In scope. Keep only citations we actually retrieved.
    cited = [sid for sid in dict.fromkeys(draft.cited_sop_ids) if sid in retrieved_sop_ids]
    dropped = sorted(set(draft.cited_sop_ids) - set(cited))

    is_security = draft.category is TicketCategory.SECURITY_INCIDENT
    severity = draft.severity
    if is_security and severity in (SeverityLevel.LOW, SeverityLevel.MEDIUM):
        severity = SeverityLevel.HIGH

    escalate = (
        draft.requires_tier2_escalation
        or is_security
        or draft.confidence_score < settings.confidence_threshold
        or not cited
    )

    reply = draft.draft_user_reply
    extra_notes: list[str] = []
    if dropped:
        extra_notes.append(f"Removed citations not in retrieved SOPs: {', '.join(dropped)}")

    if escalate:
        grounded = bool(cited) and draft.confidence_score >= settings.confidence_threshold
        if is_security:
            # Keep SOP-grounded immediate steps if we have them; else the safe template.
            if not grounded:
                extra_notes.append(f"Unsent AI draft:\n{draft.draft_user_reply}")
                reply = security_message(settings, payload.ticket_id)
        else:
            extra_notes.append(f"Unsent AI draft:\n{draft.draft_user_reply}")
            reply = handoff_message(settings, payload.ticket_id)
            cited = [] if not grounded else cited

    return AgentTriageResult(
        scope=ScopeVerdict.IN_SCOPE,
        category=draft.category,
        severity=severity,
        confidence_score=draft.confidence_score,
        requires_tier2_escalation=escalate,
        cited_sop_ids=cited,
        draft_user_reply=reply,
        internal_tech_notes=_notes(notes_prefix, draft.internal_tech_notes, *extra_notes),
    )


def unavailable_result(payload: IncomingTicketWebhook, settings: Settings, reason: str) -> AgentTriageResult:
    """Fail closed: no model answer (error or budget cap) means a human takes it."""
    return AgentTriageResult(
        scope=ScopeVerdict.IN_SCOPE,
        category=TicketCategory.UNKNOWN,
        severity=SeverityLevel.MEDIUM,
        confidence_score=0.0,
        requires_tier2_escalation=True,
        cited_sop_ids=[],
        draft_user_reply=unavailable_message(settings, payload.ticket_id),
        internal_tech_notes=f"Automatic triage skipped: {reason}.",
    )
