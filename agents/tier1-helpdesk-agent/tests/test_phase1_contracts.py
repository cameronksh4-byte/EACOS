"""Phase 1: config feature flags and schema-level hard guardrails."""

from datetime import date
from uuid import uuid4

import anthropic
import pytest
from pydantic import ValidationError

from app.config import FEATURE_MIN_TIER, Feature, LLMProvider, ServiceTier, Settings
from app.schemas.ticket import (
    AgentTriageResult,
    ApprovalCallback,
    IncomingTicketWebhook,
    LLMSOPSynthesis,
    LLMTriageOutput,
    LLMVisionExtraction,
    OSPlatform,
    ScopeVerdict,
    SeverityLevel,
    SOPPlatform,
    TicketCategory,
    TicketTelemetryEvent,
    TriageAction,
    UserContext,
)

TIER0, TIER1, TIER2 = ServiceTier.TIER_0_INCENTIVE, ServiceTier.TIER_1_PRODUCTION, ServiceTier.TIER_2_MANAGED_OPS


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


# ---------------------------------------------------------------------------
# Tier feature flags
# ---------------------------------------------------------------------------


def test_default_tier_is_the_free_incentive_tier():
    assert settings().service_tier is TIER0


@pytest.mark.parametrize(
    "tier, enabled, disabled",
    [
        (
            TIER0,
            {Feature.TRIAGE_AND_DRAFT, Feature.SCREENSHOT_VISION, Feature.OUTAGE_DETECTION, Feature.ROI_TELEMETRY},
            {Feature.CONTEXT_ENRICHMENT, Feature.MULTI_TURN, Feature.MANAGER_APPROVALS,
             Feature.KNOWLEDGE_FLYWHEEL, Feature.EXECUTIVE_REPORTS},
        ),
        (
            TIER1,
            {Feature.SCREENSHOT_VISION, Feature.CONTEXT_ENRICHMENT, Feature.MULTI_TURN, Feature.MANAGER_APPROVALS},
            {Feature.KNOWLEDGE_FLYWHEEL, Feature.EXECUTIVE_REPORTS},
        ),
        (TIER2, set(Feature), set()),
    ],
)
def test_tier_unlocks_exactly_its_features(tier, enabled, disabled):
    s = settings(service_tier=tier)
    assert enabled <= set(s.enabled_features())
    assert not (disabled & set(s.enabled_features()))


def test_tiers_are_cumulative():
    for feature, minimum in FEATURE_MIN_TIER.items():
        for tier in ServiceTier:
            assert settings(service_tier=tier).feature_enabled(feature) == tier.at_least(minimum)


def test_kill_switch_can_disable_but_never_enable(monkeypatch):
    monkeypatch.setenv("SERVICE_TIER", "TIER_1_PRODUCTION")
    monkeypatch.setenv("DISABLED_FEATURES", "multi_turn, outage_detection")
    s = Settings(_env_file=None)
    assert not s.feature_enabled(Feature.MULTI_TURN)
    assert not s.feature_enabled(Feature.OUTAGE_DETECTION)
    assert s.feature_enabled(Feature.MANAGER_APPROVALS)
    assert not s.feature_enabled(Feature.KNOWLEDGE_FLYWHEEL)  # above tier: still off


def test_unknown_feature_name_is_rejected(monkeypatch):
    monkeypatch.setenv("DISABLED_FEATURES", "telepathy")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_trial_expiry_only_applies_to_tier0():
    s0 = settings(service_tier=TIER0, trial_expires_on=date(2026, 12, 1))
    assert not s0.trial_expired(today=date(2026, 12, 1))
    assert s0.trial_expired(today=date(2026, 12, 2))
    s1 = settings(service_tier=TIER1, trial_expires_on=date(2026, 12, 1))
    assert not s1.trial_expired(today=date(2027, 1, 1))


def test_tier_specific_settings_are_required_at_startup():
    base = dict(
        tenant_id="acme-corp", webhook_secret="s", anthropic_api_key="k",
        supabase_url="https://x.supabase.co", supabase_service_key="k", voyage_api_key="v",
    )
    assert settings(service_tier=TIER0, **base).missing_core_settings() == []
    missing1 = settings(service_tier=TIER1, **base).missing_core_settings()
    assert "N8N_APPROVAL_WEBHOOK_URL" in missing1 and "N8N_CALLBACK_SECRET" in missing1
    missing2 = settings(service_tier=TIER2, **base).missing_core_settings()
    assert "N8N_NOTIFY_WEBHOOK_URL" in missing2
    # Killing a feature drops its requirements.
    s = settings(service_tier=TIER1, disabled_features="manager_approvals", **base)
    assert s.missing_core_settings() == []


def test_bedrock_needs_no_anthropic_key_and_prefixes_model_ids():
    s = settings(llm_provider=LLMProvider.BEDROCK, tenant_id="acme-corp", webhook_secret="s",
                 supabase_url="u", supabase_service_key="k", voyage_api_key="v")
    assert s.missing_core_settings() == []
    assert s.provider_model_id("claude-haiku-5-5") == "anthropic.claude-haiku-5-5"
    assert s.price_for("anthropic.claude-haiku-5-5") == (0.10, 0.50)


def test_default_models_are_current_not_retired():
    s = settings()
    assert s.triage_model == s.vision_model == "claude-haiku-5-5"
    assert s.reasoning_model == "claude-sonnet-5-5"


# ---------------------------------------------------------------------------
# Tenancy and budget
# ---------------------------------------------------------------------------


def test_allowed_tenants_from_csv_include_default_tenant(monkeypatch):
    monkeypatch.setenv("TENANT_ID", "acme-corp")
    monkeypatch.setenv("ALLOWED_TENANT_IDS", "globex, initech")
    s = Settings(_env_file=None)
    assert s.allowed_tenant_ids == ["acme-corp", "globex", "initech"]
    assert s.is_tenant_allowed("initech") and not s.is_tenant_allowed("umbrella")


def test_per_tenant_budget_override():
    s = settings(monthly_token_budget_usd=25, tenant_budget_overrides_usd={"globex": 100})
    assert s.budget_for("globex") == 100 and s.budget_for("acme-corp") == 25


def test_confidence_threshold_cannot_go_below_hard_floor():
    with pytest.raises(ValidationError):
        settings(confidence_threshold=0.7)


def test_clarifying_questions_capped_at_two():
    with pytest.raises(ValidationError):
        settings(max_clarifying_questions=3)


# ---------------------------------------------------------------------------
# Inbound ticket
# ---------------------------------------------------------------------------


def _ticket(**kw):
    base = dict(tenant_id="acme-corp", ticket_id="T1", reporter_email="Pat@Acme.com", subject="s", body="b")
    base.update(kw)
    return IncomingTicketWebhook(**base)


def test_ticket_normalises_email_and_accepts_https_attachments():
    t = _ticket(attachment_urls=["https://files.slack.com/a.png"])
    assert t.reporter_email == "pat@acme.com"
    assert str(t.attachment_urls[0]).startswith("https://")
    assert t.conversation_key == "T1"


@pytest.mark.parametrize("bad", [{"attachment_urls": ["http://x.com/a.png"]}, {"tenant_id": "ACME"},
                                 {"source_system": "email"}, {"unexpected": 1}])
def test_ticket_rejects_bad_input(bad):
    with pytest.raises(ValidationError):
        _ticket(**bad)


# ---------------------------------------------------------------------------
# Hard guardrail #2, enforced at construction
# ---------------------------------------------------------------------------


def _result(**kw):
    base = dict(category=TicketCategory.VPN_NETWORK, severity=SeverityLevel.MEDIUM, confidence_score=0.95,
                requires_tier2_escalation=False, cited_sop_ids=["SOP-NET-001"], draft_user_reply="1. ...")
    base.update(kw)
    return AgentTriageResult(**base)


def test_grounded_confident_resolution_is_not_escalated():
    r = _result()
    assert r.action is TriageAction.RESOLVE and not r.requires_tier2_escalation and r.deflected


@pytest.mark.parametrize(
    "override",
    [{"confidence_score": 0.79}, {"cited_sop_ids": []}, {"category": TicketCategory.SECURITY_INCIDENT}],
)
def test_guardrail_forces_escalation(override):
    r = _result(**override)
    assert r.requires_tier2_escalation is True
    assert r.action is TriageAction.ESCALATE_TIER_2
    assert not r.deflected


def test_security_incident_escalates_even_when_asking_a_question():
    r = _result(category=TicketCategory.SECURITY_INCIDENT, action=TriageAction.ASK_CLARIFICATION,
                clarifying_question="Did you enter your password?")
    assert r.requires_tier2_escalation is True


def test_clarifying_question_is_not_forced_to_escalate_and_needs_text():
    r = _result(action=TriageAction.ASK_CLARIFICATION, cited_sop_ids=[], confidence_score=0.5,
                clarifying_question="Which VPN client do you use?")
    assert r.action is TriageAction.ASK_CLARIFICATION and not r.requires_tier2_escalation
    with pytest.raises(ValidationError):
        _result(action=TriageAction.ASK_CLARIFICATION, clarifying_question=None)


def test_outage_broadcast_needs_parent_incident():
    r = _result(active_outage_detected=True, parent_incident_id=uuid4(), cited_sop_ids=[])
    assert r.active_outage_detected and not r.requires_tier2_escalation
    with pytest.raises(ValidationError):
        _result(active_outage_detected=True)


def test_approval_wait_needs_approval_id():
    with pytest.raises(ValidationError):
        _result(action=TriageAction.AWAIT_MANAGER_APPROVAL)
    assert _result(action=TriageAction.AWAIT_MANAGER_APPROVAL, approval_id=uuid4()).approval_id


def test_beyond_tier1_always_escalates_and_decline_is_not_deflection():
    r = _result(scope=ScopeVerdict.BEYOND_TIER1)
    assert r.requires_tier2_escalation and r.action is TriageAction.ESCALATE_TIER_2
    d = _result(scope=ScopeVerdict.NOT_IT_SUPPORT, cited_sop_ids=[], category=TicketCategory.UNKNOWN)
    assert not d.requires_tier2_escalation and not d.deflected


# ---------------------------------------------------------------------------
# Other contracts
# ---------------------------------------------------------------------------


def test_user_context_platform_filter_and_immutability():
    ctx = UserContext(email="pat@acme.com", os_platform=OSPlatform.MACOS)
    assert ctx.sop_platform_filter is SOPPlatform.MACOS
    assert UserContext(email="x@y.z").sop_platform_filter is None
    with pytest.raises(ValidationError):
        ctx.vip_status = True  # frozen


def test_approval_callback_validation():
    ok = ApprovalCallback(approval_id=uuid4(), callback_nonce="a" * 48, tenant_id="acme-corp",
                          decision="approved", decided_by_email="Boss@Acme.com")
    assert ok.decided_by_email == "boss@acme.com"
    with pytest.raises(ValidationError):
        ApprovalCallback(approval_id=uuid4(), callback_nonce="short", tenant_id="acme-corp",
                         decision="approved", decided_by_email="boss@acme.com")
    with pytest.raises(ValidationError):
        ApprovalCallback(approval_id=uuid4(), callback_nonce="a" * 48, tenant_id="acme-corp",
                         decision="maybe", decided_by_email="boss@acme.com")


def test_telemetry_minutes_only_for_deflected():
    base = dict(tenant_id="acme-corp", ticket_id="T", service_tier="TIER_0_INCENTIVE",
                category=TicketCategory.UNKNOWN, action=TriageAction.ESCALATE_TIER_2,
                scope=ScopeVerdict.IN_SCOPE, confidence_score=0.4)
    assert TicketTelemetryEvent(**base, deflected_bool=False).estimated_minutes_saved == 0
    with pytest.raises(ValidationError):
        TicketTelemetryEvent(**base, deflected_bool=False, estimated_minutes_saved=15)


@pytest.mark.parametrize("model", [LLMTriageOutput, LLMVisionExtraction, LLMSOPSynthesis])
def test_llm_output_schemas_are_strict_and_fully_required(model):
    schema = anthropic.transform_schema(model)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(model.model_fields)
