from app.config import get_settings
from app.schemas.ticket import (
    IncomingTicketWebhook,
    LLMTriageOutput,
    ScopeVerdict,
    SeverityLevel,
    TicketCategory,
    TriageAction,
)
from app.services.guardrails import enforce_policy

TICKET = IncomingTicketWebhook(
    tenant_id="acme-corp",
    ticket_id="T-1",
    reporter_email="pat@acme.com",
    subject="help",
    body="help",
)


def draft(**overrides) -> LLMTriageOutput:
    base = dict(
        scope=ScopeVerdict.IN_SCOPE,
        action=TriageAction.RESOLVE,
        category=TicketCategory.PASSWORD_AUTH,
        severity=SeverityLevel.MEDIUM,
        confidence_score=0.93,
        requires_tier2_escalation=False,
        cited_sop_ids=["SOP-AUTH-001"],
        draft_user_reply="1. Go to the reset portal...",
        clarifying_question="",
        requested_item="",
        missing_sop_topic="",
        known_facts=[],
        internal_tech_notes="",
    )
    base.update(overrides)
    return LLMTriageOutput(**base)


def run(d: LLMTriageOutput, retrieved=frozenset({"SOP-AUTH-001"})):
    return enforce_policy(TICKET, d, set(retrieved), get_settings(), "claude-haiku-5-5")


def test_grounded_in_scope_reply_is_sent_unchanged():
    result = run(draft())
    assert result.requires_tier2_escalation is False
    assert result.draft_user_reply == "1. Go to the reset portal..."
    assert result.cited_sop_ids == ["SOP-AUTH-001"]


def test_not_it_request_gets_cordial_template_and_no_model_text():
    result = run(
        draft(
            scope=ScopeVerdict.NOT_IT_SUPPORT,
            category=TicketCategory.UNKNOWN,
            draft_user_reply="Sure! Here's your essay on the French Revolution...",
            cited_sop_ids=["SOP-AUTH-001"],
        )
    )
    assert result.scope is ScopeVerdict.NOT_IT_SUPPORT
    assert result.requires_tier2_escalation is False
    assert result.cited_sop_ids == []
    assert "French Revolution" not in result.draft_user_reply
    assert "thanks for reaching out" in result.draft_user_reply.lower()
    assert "outside what I can help with" in result.draft_user_reply


def test_beyond_tier1_is_declined_politely_and_escalated():
    result = run(
        draft(scope=ScopeVerdict.BEYOND_TIER1, category=TicketCategory.SOFTWARE_ACCESS,
              draft_user_reply="Run Set-ADUser ...")
    )
    assert result.requires_tier2_escalation is True
    assert "Set-ADUser" not in result.draft_user_reply
    assert "beyond what the Tier 1 help desk" in result.draft_user_reply
    assert "T-1" in result.draft_user_reply


def test_security_incident_always_escalates_and_is_at_least_p2():
    result = run(draft(category=TicketCategory.SECURITY_INCIDENT, severity=SeverityLevel.LOW))
    assert result.requires_tier2_escalation is True
    assert result.severity is SeverityLevel.HIGH


def test_low_confidence_escalates_and_hides_unverified_draft_from_user():
    result = run(draft(confidence_score=0.55))
    assert result.requires_tier2_escalation is True
    assert "reset portal" not in result.draft_user_reply
    assert "reset portal" in (result.internal_tech_notes or "")


def test_invented_citation_is_removed_and_forces_escalation():
    result = run(draft(cited_sop_ids=["SOP-MADE-UP"]))
    assert result.cited_sop_ids == []
    assert result.requires_tier2_escalation is True
    assert "SOP-MADE-UP" in (result.internal_tech_notes or "")


def test_model_requesting_escalation_is_respected():
    result = run(draft(requires_tier2_escalation=True))
    assert result.requires_tier2_escalation is True
