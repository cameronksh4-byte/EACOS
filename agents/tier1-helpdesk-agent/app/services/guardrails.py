"""Deterministic policy applied after the model answers.

The model proposes; this module decides. In particular:

* Out-of-scope requests always get a fixed, courteous template, never
  model-written text. Tone stays consistent, and no partial "help" with an
  out-of-scope request leaks through.
* Cited SOP IDs must be ones we actually retrieved (no invented citations).
* Security incidents, low confidence, and uncited answers always escalate.
* When a ticket escalates, the requester gets a handoff note. An unverified
  draft is passed to the technician, never sent to the user.
* The hard guardrails themselves (guardrail #2 and its exemptions) are enforced
  by `AgentTriageResult`'s validator. This module picks the user-facing text
  that matches the final, validated action.
"""

from __future__ import annotations

from uuid import UUID

from app.config import Settings
from app.schemas.ticket import (
    AgentTriageResult,
    IncomingTicketWebhook,
    LLMTriageOutput,
    ScopeVerdict,
    SeverityLevel,
    TicketCategory,
    TriageAction,
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


def outage_message(settings: Settings, ticket_id: str, incident_id: UUID) -> str:
    # Deliberately generic: never quote other people's tickets back to this user.
    return (
        "Hi there, and thanks for letting us know!\n\n"
        "We're seeing several reports like yours right now, so this looks like a wider service issue "
        "rather than something on your device. Our team is already working on it, so there's no need "
        "to troubleshoot on your end.\n\n"
        "We'll post an update as soon as it's fixed. Your reference number is "
        f"{ticket_id} (incident {str(incident_id)[:8]}). Thank you for your patience!"
    )


def security_outage_message(settings: Settings, ticket_id: str, incident_id: UUID) -> str:
    return (
        "Hi there, and thank you for reporting this. It really helps keep everyone safe.\n\n"
        "Other colleagues have reported something similar, and our security team is already on it.\n"
        "1. Please don't click any links or open attachments in the suspicious message.\n"
        "2. Don't delete the message. The team may need it.\n"
        "3. If you entered your password anywhere suspicious, tell us in a reply. "
        "Please don't send the password itself.\n\n"
        f"Reference: {ticket_id} (incident {str(incident_id)[:8]})."
    )


def approval_pending_message(settings: Settings, ticket_id: str, item: str) -> str:
    return (
        "Hi there, and thanks for your request!\n\n"
        f"{item} needs your manager's approval, so I've sent them a request. As soon as they approve "
        "it, we'll get it set up for you, and I'll let you know either way.\n\n"
        f"Your reference number is {ticket_id}."
    )


def clarification_message(question: str) -> str:
    return f"Thanks for the details! So I can point you to the right fix, one quick question:\n\n{question}"


def _notes(*parts: str | None) -> str | None:
    text = "\n".join(p.strip() for p in parts if p and p.strip())
    return text or None


def enforce_policy(
    payload: IncomingTicketWebhook,
    draft: LLMTriageOutput,
    retrieved_sop_ids: set[str],
    settings: Settings,
    model_used: str,
    *,
    clarifications_used: int = 0,
    approval_id: UUID | None = None,
    approval_item_name: str | None = None,
) -> AgentTriageResult:
    """Turn the model's draft into a validated result with the right reply text.

    `clarifications_used` and `approval_id` come from the Tier 1 conversation
    and approval services. Without them, those actions can't pass the
    validator and fall back to escalation.
    """
    common = dict(
        ticket_id=payload.ticket_id,
        thread_id=payload.thread_id,
        service_tier=settings.service_tier.value,
        model_used=model_used,
    )
    notes_prefix = f"[triaged by {model_used}]"

    # 1. Not IT support: courteous decline, nothing else.
    if draft.scope is ScopeVerdict.NOT_IT_SUPPORT:
        return AgentTriageResult(
            **common,
            scope=ScopeVerdict.NOT_IT_SUPPORT,
            category=TicketCategory.UNKNOWN,
            severity=SeverityLevel.LOW,
            confidence_score=draft.confidence_score,
            requires_tier2_escalation=False,
            draft_user_reply=decline_not_it(settings),
            internal_tech_notes=_notes(notes_prefix, "Declined: not an IT support request.", draft.internal_tech_notes),
        )

    # 2. IT, but beyond Tier 1: politely decline to act and hand to Tier 2.
    if draft.scope is ScopeVerdict.BEYOND_TIER1:
        return AgentTriageResult(
            **common,
            scope=ScopeVerdict.BEYOND_TIER1,
            category=draft.category,
            severity=draft.severity,
            confidence_score=draft.confidence_score,
            requires_tier2_escalation=True,
            draft_user_reply=decline_beyond_tier1(settings, payload.ticket_id),
            internal_tech_notes=_notes(notes_prefix, "Beyond Tier 1 scope; escalated.", draft.internal_tech_notes),
        )

    # 3. In scope. Keep only citations we actually retrieved.
    cited = [sid for sid in dict.fromkeys(draft.cited_sop_ids) if sid in retrieved_sop_ids]
    dropped = sorted(set(draft.cited_sop_ids) - set(cited))
    is_security = draft.category is TicketCategory.SECURITY_INCIDENT
    severity = draft.severity
    if is_security and severity in (SeverityLevel.LOW, SeverityLevel.MEDIUM):
        severity = SeverityLevel.HIGH

    gap = draft.missing_sop_topic.strip()
    if not cited and not gap and draft.action is not TriageAction.ASK_CLARIFICATION:
        gap = f"{draft.category.value.replace('_', ' ')}: undocumented issue"

    # The validator applies guardrail #2 and drops unbacked exemptions.
    result = AgentTriageResult(
        **common,
        scope=ScopeVerdict.IN_SCOPE,
        action=draft.action,
        category=draft.category,
        severity=severity,
        confidence_score=draft.confidence_score,
        requires_tier2_escalation=draft.requires_tier2_escalation,
        cited_sop_ids=cited,
        draft_user_reply=draft.draft_user_reply,
        clarifying_question=draft.clarifying_question.strip() or None,
        clarifications_used=clarifications_used,
        approval_id=approval_id,
        missing_sop_topic=(gap or None) if not cited else None,
    )

    notes: list[str] = [notes_prefix, draft.internal_tech_notes]
    if dropped:
        notes.append(f"Removed citations not in retrieved SOPs: {', '.join(dropped)}")
    if draft.requested_item:
        notes.append(f"Requested item: {draft.requested_item}")
    if result.guardrail_overrides:
        notes.append(f"Guardrail overrides: {', '.join(result.guardrail_overrides)}")

    grounded = bool(cited) and draft.confidence_score >= settings.confidence_threshold
    if result.action is TriageAction.RESOLVE:
        reply = draft.draft_user_reply
    elif result.action is TriageAction.ASK_CLARIFICATION:
        reply = clarification_message(result.clarifying_question or "")
        if is_security:
            reply = security_message(settings, payload.ticket_id) + "\n\n" + (result.clarifying_question or "")
    elif result.action is TriageAction.AWAIT_MANAGER_APPROVAL:
        reply = approval_pending_message(settings, payload.ticket_id, approval_item_name or draft.requested_item or "This item")
    elif is_security:
        # Keep SOP-grounded immediate steps if we have them; else the safe template.
        reply = draft.draft_user_reply if grounded else security_message(settings, payload.ticket_id)
        if not grounded:
            notes.append(f"Unsent AI draft:\n{draft.draft_user_reply}")
    else:
        reply = handoff_message(settings, payload.ticket_id)
        notes.append(f"Unsent AI draft:\n{draft.draft_user_reply}")

    return result.model_copy(update={"draft_user_reply": reply, "internal_tech_notes": _notes(*notes)})


def outage_result(
    payload: IncomingTicketWebhook,
    settings: Settings,
    incident_id: UUID,
    category: TicketCategory,
    matched_tickets: int,
) -> AgentTriageResult:
    """Immediate broadcast for a ticket in an active outage cluster (no model call)."""
    is_security = category is TicketCategory.SECURITY_INCIDENT
    reply = (security_outage_message if is_security else outage_message)(settings, payload.ticket_id, incident_id)
    return AgentTriageResult(
        ticket_id=payload.ticket_id,
        thread_id=payload.thread_id,
        service_tier=settings.service_tier.value,
        scope=ScopeVerdict.IN_SCOPE,
        action=TriageAction.RESOLVE,
        category=category,
        severity=SeverityLevel.CRITICAL,
        confidence_score=1.0,
        requires_tier2_escalation=False,  # parent incident is escalated; security still forces True
        draft_user_reply=reply,
        active_outage_detected=True,
        parent_incident_id=incident_id,
        internal_tech_notes=f"Joined outage incident {incident_id} ({matched_tickets} similar tickets in window).",
    )


def unavailable_result(payload: IncomingTicketWebhook, settings: Settings, reason: str) -> AgentTriageResult:
    """Fail closed: no model answer (error, budget cap, trial ended) means a human takes it."""
    return AgentTriageResult(
        ticket_id=payload.ticket_id,
        thread_id=payload.thread_id,
        service_tier=settings.service_tier.value,
        scope=ScopeVerdict.IN_SCOPE,
        action=TriageAction.ESCALATE_TIER_2,
        category=TicketCategory.UNKNOWN,
        severity=SeverityLevel.MEDIUM,
        confidence_score=0.0,
        requires_tier2_escalation=True,
        draft_user_reply=unavailable_message(settings, payload.ticket_id),
        internal_tech_notes=f"Automatic triage skipped: {reason}.",
    )
