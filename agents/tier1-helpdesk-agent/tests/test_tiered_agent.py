"""Cross-cutting invariants: deterministic guardrails and tier gating.

The phase test files check behaviour with examples. This file checks
*guarantees*, exhaustively where the input space is small enough:

  Guardrail #1  no user-facing citation that wasn't retrieved from pgvector
  Guardrail #2  escalation is forced (or an exemption is backed by its control),
                checked over every combination of the fields that matter
  Guardrail #3  no model call without a budget reservation first, at every tier
  Tier gating   every route and every gated service is available at exactly the
                tiers it should be, and kill switches only ever remove features
"""

from __future__ import annotations

import inspect
import itertools
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.config import FEATURE_MIN_TIER, Feature, ServiceTier, Settings, get_settings
from app.schemas.ticket import (
    CONFIDENCE_THRESHOLD,
    AgentTriageResult,
    IncomingTicketWebhook,
    LLMTriageOutput,
    ScopeVerdict,
    SeverityLevel,
    TicketCategory,
    TriageAction,
)
from app.services import (
    background,
    context_enrichment,
    flywheel,
    guardrails,
    pipeline,
    reports,
    vision,
)
from tests.fakes import triage_output

TIER_ENV = {
    ServiceTier.TIER_0_INCENTIVE: {"SERVICE_TIER": "TIER_0_INCENTIVE"},
    ServiceTier.TIER_1_PRODUCTION: {
        "SERVICE_TIER": "TIER_1_PRODUCTION",
        "PUBLIC_BASE_URL": "https://helpdesk.acme.com",
        "N8N_APPROVAL_WEBHOOK_URL": "https://n8n.acme.com/webhook/approvals",
        "N8N_OUTBOUND_SIGNING_SECRET": "outbound-secret",
        "N8N_CALLBACK_SECRET": "callback-secret",
    },
}
TIER_ENV[ServiceTier.TIER_2_MANAGED_OPS] = {
    **TIER_ENV[ServiceTier.TIER_1_PRODUCTION],
    "SERVICE_TIER": "TIER_2_MANAGED_OPS",
    "N8N_NOTIFY_WEBHOOK_URL": "https://n8n.acme.com/webhook/notify",
}


def set_tier(monkeypatch, tier: ServiceTier, **extra: str) -> None:
    for k, v in {**TIER_ENV[tier], **extra}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def ticket(**kw) -> IncomingTicketWebhook:
    base = dict(tenant_id="acme-corp", ticket_id="T-1", thread_id="slack:C1:1.1", reporter_email="pat@acme.com",
                subject="VPN won't connect", body="GlobalProtect fails", source_system="slack")
    base.update(kw)
    return IncomingTicketWebhook(**base)


# =============================================================================
# Guardrail #2: exhaustive invariants over AgentTriageResult
# =============================================================================

GRID = list(itertools.product(
    list(ScopeVerdict),
    list(TriageAction),
    [TicketCategory.VPN_NETWORK, TicketCategory.SECURITY_INCIDENT],
    [CONFIDENCE_THRESHOLD - 0.01, CONFIDENCE_THRESHOLD],
    [[], ["SOP-1"]],
    [False, True],          # model asked for escalation
    [False, True],          # outage broadcast with a parent incident
    [None, "slack:C1:1"],   # thread id
    [0, 1, 2],              # clarifications used
    [False, True],          # approval record present
))


def build(scope, action, category, conf, cited, requested, outage, thread, used, approval) -> AgentTriageResult:
    return AgentTriageResult(
        scope=scope, action=action, category=category, severity=SeverityLevel.MEDIUM, confidence_score=conf,
        requires_tier2_escalation=requested, cited_sop_ids=list(cited), draft_user_reply="reply",
        clarifying_question="Which VPN?", thread_id=thread, clarifications_used=used,
        approval_id=uuid.uuid4() if approval else None,
        active_outage_detected=outage, parent_incident_id=uuid.uuid4() if outage else None,
    )


def test_grid_is_exhaustive():
    assert len(GRID) == 3 * 4 * 2 * 2 * 2 * 2 * 2 * 2 * 3 * 2


@pytest.mark.parametrize("scope", list(ScopeVerdict))
def test_guardrail2_invariants_hold_for_every_combination(scope):
    for combo in (c for c in GRID if c[0] is scope):
        r = build(*combo)
        ctx = f"input={combo} -> action={r.action} escalate={r.requires_tier2_escalation}"

        if r.scope is ScopeVerdict.NOT_IT_SUPPORT:
            # Courteous decline: no steps, no citations, no escalation.
            assert r.cited_sop_ids == [] and not r.requires_tier2_escalation, ctx
            assert r.action is TriageAction.RESOLVE and r.category is TicketCategory.UNKNOWN, ctx
            continue
        if r.scope is ScopeVerdict.BEYOND_TIER1:
            assert r.action is TriageAction.ESCALATE_TIER_2 and r.requires_tier2_escalation, ctx
            continue

        # In scope.
        if r.category is TicketCategory.SECURITY_INCIDENT:
            assert r.requires_tier2_escalation, ctx
        if r.action is TriageAction.RESOLVE and not r.active_outage_detected:
            # Troubleshooting steps reach the user only when fully grounded.
            assert r.confidence_score >= CONFIDENCE_THRESHOLD and r.cited_sop_ids, ctx
            assert r.category is not TicketCategory.SECURITY_INCIDENT and not r.requires_tier2_escalation, ctx
        if r.action is TriageAction.ASK_CLARIFICATION:
            assert r.thread_id and r.clarifying_question and 1 <= r.clarifications_used <= 2, ctx
        if r.action is TriageAction.AWAIT_MANAGER_APPROVAL:
            assert r.approval_id is not None, ctx
        if r.active_outage_detected:
            assert r.parent_incident_id is not None, ctx
        if r.action is TriageAction.ESCALATE_TIER_2:
            assert r.requires_tier2_escalation, ctx
        if r.deflected:
            assert (r.action is TriageAction.RESOLVE and r.cited_sop_ids and not r.requires_tier2_escalation
                    and not r.active_outage_detected), ctx


def test_every_non_resolving_outcome_without_its_control_escalates():
    """The four exemptions apply only while their control is present (fail closed)."""
    for combo in GRID:
        scope, action, category, conf, cited, requested, outage, thread, used, approval = combo
        if scope is not ScopeVerdict.IN_SCOPE or category is TicketCategory.SECURITY_INCIDENT:
            continue
        r = build(*combo)
        if action is TriageAction.ASK_CLARIFICATION and not (thread and used >= 1):
            assert r.action is TriageAction.ESCALATE_TIER_2, combo
        if action is TriageAction.AWAIT_MANAGER_APPROVAL and not approval:
            assert r.action is TriageAction.ESCALATE_TIER_2, combo


def test_overrides_are_always_recorded():
    r = build(ScopeVerdict.IN_SCOPE, TriageAction.RESOLVE, TicketCategory.VPN_NETWORK, 0.5, [], False,
              False, None, 0, False)
    assert r.guardrail_overrides == ["confidence_below_threshold"]
    assert build(ScopeVerdict.IN_SCOPE, TriageAction.RESOLVE, TicketCategory.VPN_NETWORK, 0.9, ["S"], False,
                 False, None, 0, False).guardrail_overrides == []


def test_threshold_is_a_hard_floor():
    with pytest.raises(ValueError):
        Settings(_env_file=None, confidence_threshold=CONFIDENCE_THRESHOLD - 0.05)


# =============================================================================
# Guardrail #1: citations only from what was retrieved
# =============================================================================


@pytest.mark.parametrize("claimed", [[], ["SOP-FAKE"], ["SOP-A", "SOP-FAKE"], ["SOP-A", "SOP-A", "SOP-B"]])
@pytest.mark.parametrize("action", [TriageAction.RESOLVE, TriageAction.ESCALATE_TIER_2])
def test_enforce_policy_never_cites_unretrieved_docs(claimed, action):
    draft = LLMTriageOutput.model_validate(triage_output(action=action.value, cited_sop_ids=claimed))
    result = guardrails.enforce_policy(ticket(), draft, {"SOP-A", "SOP-B"}, get_settings(), "claude-haiku-5-5")
    assert set(result.cited_sop_ids) <= {"SOP-A", "SOP-B"}
    assert len(result.cited_sop_ids) == len(set(result.cited_sop_ids))
    if result.action is TriageAction.RESOLVE:
        assert result.cited_sop_ids and result.draft_user_reply == draft.draft_user_reply
    else:
        assert result.draft_user_reply != draft.draft_user_reply  # unverified draft never sent


@pytest.mark.parametrize("tier", list(ServiceTier))
async def test_pipeline_only_cites_retrieved_docs(env, monkeypatch, tier):
    set_tier(monkeypatch, tier)
    env["claude"].triage = [triage_output(cited_sop_ids=["SOP-NET-001", "SOP-INVENTED-9"])]
    result = await pipeline.process_ticket(ticket())
    retrieved = {s["doc_id"] for s in env["sb"].sops}
    assert set(result.cited_sop_ids) <= retrieved


# =============================================================================
# Guardrail #3: no model call without a reservation, at every tier
# =============================================================================

SCENARIOS = {
    "resolve": [triage_output()],
    "low_confidence_second_opinion": [triage_output(confidence_score=0.5), triage_output()],
    "decline": [triage_output(scope="not_it_support", category="unknown", cited_sop_ids=[])],
    "clarify": [triage_output(action="ASK_CLARIFICATION", clarifying_question="Which VPN?", cited_sop_ids=[],
                              confidence_score=0.6)],
}


@pytest.mark.parametrize("tier", list(ServiceTier))
@pytest.mark.parametrize("scenario", list(SCENARIOS))
async def test_every_model_call_is_preceded_by_a_reservation(env, monkeypatch, tier, scenario):
    set_tier(monkeypatch, tier)
    env["claude"].triage = SCENARIOS[scenario]
    await pipeline.process_ticket(ticket())
    await background.drain()
    assert len(env["sb"].reservations) == len(env["claude"].requests) >= 1
    assert len(env["sb"].settled) == len(env["sb"].reservations)  # every reservation settled
    cap = env["sb"].reservations[0]["p_cap_usd"]
    assert cap == get_settings().monthly_token_budget_usd


@pytest.mark.parametrize("tier", list(ServiceTier))
@pytest.mark.parametrize("failure", ["cap_reached", "ledger_down"])
async def test_no_model_call_when_budget_cannot_be_reserved(env, monkeypatch, tier, failure):
    set_tier(monkeypatch, tier)
    if failure == "cap_reached":
        env["sb"].cap_ok = False
    else:
        env["sb"].ledger_down = True
    result = await pipeline.process_ticket(ticket())
    assert env["claude"].requests == []
    assert result.requires_tier2_escalation and result.action is TriageAction.ESCALATE_TIER_2


def test_per_tenant_budget_override_reaches_the_reservation(env, monkeypatch):
    monkeypatch.setenv("TENANT_BUDGET_OVERRIDES_USD", '{"acme-corp": 99.5}')
    get_settings.cache_clear()
    import asyncio

    asyncio.run(pipeline.process_ticket(ticket()))
    assert env["sb"].reservations[0]["p_cap_usd"] == 99.5


# =============================================================================
# Tier gating: routes
# =============================================================================

GATED_ROUTES = {
    ("POST", "/v1/triage"): Feature.TRIAGE_AND_DRAFT,
    ("GET", "/v1/telemetry/{tenant_id}/roi-summary"): Feature.ROI_TELEMETRY,
    ("POST", "/v1/callbacks/approval"): Feature.MANAGER_APPROVALS,
    ("POST", "/v1/webhooks/ticket-resolved"): Feature.KNOWLEDGE_FLYWHEEL,
    ("POST", "/v1/callbacks/sop-draft"): Feature.KNOWLEDGE_FLYWHEEL,
    ("POST", "/v1/reports/weekly"): Feature.EXECUTIVE_REPORTS,
}
UNGATED_ROUTES = {("GET", "/health"), ("POST", "/webhooks/slack"), ("POST", "/webhooks/teams"),
                  ("POST", "/webhooks/jira")}


def _gate_of(route: APIRoute) -> Feature | None:
    for dep in route.dependant.dependencies:
        name = getattr(dep.call, "__name__", "")
        if name.startswith("require_") and name.removeprefix("require_") in {f.value for f in Feature}:
            return Feature(name.removeprefix("require_"))
    return None


def test_every_v1_route_is_gated_as_declared():
    from app.main import app

    seen = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                seen[(method, route.path)] = _gate_of(route)
    assert {k: v for k, v in seen.items() if v} == GATED_ROUTES
    assert set(seen) == set(GATED_ROUTES) | UNGATED_ROUTES, "new route: declare its gate here"


@pytest.mark.parametrize("tier", list(ServiceTier))
def test_routes_exist_only_at_their_tier(env, monkeypatch, tier):
    set_tier(monkeypatch, tier)
    from app.main import app

    paths = {
        ("POST", "/v1/triage"): "/v1/triage",
        ("GET", "/v1/telemetry/{tenant_id}/roi-summary"): "/v1/telemetry/acme-corp/roi-summary",
        ("POST", "/v1/callbacks/approval"): "/v1/callbacks/approval",
        ("POST", "/v1/webhooks/ticket-resolved"): "/v1/webhooks/ticket-resolved",
        ("POST", "/v1/callbacks/sop-draft"): "/v1/callbacks/sop-draft",
        ("POST", "/v1/reports/weekly"): "/v1/reports/weekly?tenant_id=acme-corp",
    }
    with TestClient(app) as client:
        for key, feature in GATED_ROUTES.items():
            method, _ = key
            r = client.request(method, paths[key], headers={"x-webhook-secret": "wrong"}, content=b"{}")
            should_exist = tier.at_least(FEATURE_MIN_TIER[feature])
            assert (r.status_code != 404) == should_exist, f"{tier.value} {key} -> {r.status_code}"
            if should_exist:
                assert r.status_code in (401, 422), f"{key} not authenticated: {r.status_code}"


@pytest.mark.parametrize("feature", [f for f in GATED_ROUTES.values()])
def test_kill_switch_hides_route_even_at_top_tier(env, monkeypatch, feature):
    set_tier(monkeypatch, ServiceTier.TIER_2_MANAGED_OPS, DISABLED_FEATURES=feature.value)
    from app.main import app

    method, path = next(k for k, v in GATED_ROUTES.items() if v is feature)
    path = path.replace("{tenant_id}", "acme-corp")
    with TestClient(app) as client:
        assert client.request(method, path, content=b"{}").status_code == 404


# =============================================================================
# Tier gating: services and settings
# =============================================================================

GATED_SERVICES = {
    context_enrichment.enrich: Feature.CONTEXT_ENRICHMENT,
    flywheel.process_resolved_ticket: Feature.KNOWLEDGE_FLYWHEEL,
    flywheel.apply_review: Feature.KNOWLEDGE_FLYWHEEL,
    reports.weekly_report: Feature.EXECUTIVE_REPORTS,
}


def test_gated_services_declare_the_right_feature():
    for fn, feature in GATED_SERVICES.items():
        assert getattr(fn, "__gated_feature__", None) is feature, fn.__name__


@pytest.mark.parametrize("tier", list(ServiceTier))
async def test_gated_services_do_nothing_below_their_tier(env, monkeypatch, tier):
    set_tier(monkeypatch, tier)
    for fn, feature in GATED_SERVICES.items():
        if tier.at_least(FEATURE_MIN_TIER[feature]):
            continue
        params = inspect.signature(fn).parameters
        args = [None] * sum(1 for p in params.values() if p.default is inspect.Parameter.empty)
        assert await fn(*args) is None, fn.__name__
    assert env["sb"].rpc_calls == [] and env["claude"].requests == []


async def test_vision_respects_its_gate(env, monkeypatch):
    set_tier(monkeypatch, ServiceTier.TIER_2_MANAGED_OPS, DISABLED_FEATURES="screenshot_vision")
    result = await vision.analyze_attachments(ticket(attachment_urls=["https://files.slack.com/a.png"]), None)
    assert result is None


@pytest.mark.parametrize("tier", list(ServiceTier))
@pytest.mark.parametrize("disabled", [frozenset(), frozenset({Feature.MULTI_TURN}), frozenset(Feature)])
def test_kill_switch_never_widens(tier, disabled):
    s = Settings(_env_file=None, service_tier=tier, disabled_features=disabled)
    for f in Feature:
        if s.feature_enabled(f):
            assert tier.at_least(FEATURE_MIN_TIER[f]) and f not in disabled


@pytest.mark.parametrize("tier", list(ServiceTier))
def test_startup_refuses_a_tier_without_its_settings(monkeypatch, tier):
    if tier is ServiceTier.TIER_0_INCENTIVE:
        monkeypatch.delenv("SLACK_BOT_TOKEN")  # Tier 0 still needs a Tier 2 notification channel
    monkeypatch.setenv("SERVICE_TIER", tier.value)
    get_settings.cache_clear()
    from app.main import app

    with pytest.raises(RuntimeError, match="missing required settings"):
        with TestClient(app):
            pass


# =============================================================================
# Same ticket, three tiers: behaviour differs only where the gates say so
# =============================================================================


async def test_same_clarification_request_across_tiers(env, monkeypatch):
    actions = {}
    for tier in ServiceTier:
        set_tier(monkeypatch, tier)
        env["sb"].tables.clear()
        env["claude"].triage = SCENARIOS["clarify"]
        result = await pipeline.process_ticket(ticket(ticket_id=f"T-{tier.value}", thread_id=f"th-{tier.value}"))
        await background.drain()
        actions[tier] = result.action
    assert actions == {
        ServiceTier.TIER_0_INCENTIVE: TriageAction.ESCALATE_TIER_2,      # no multi-turn
        ServiceTier.TIER_1_PRODUCTION: TriageAction.ASK_CLARIFICATION,
        ServiceTier.TIER_2_MANAGED_OPS: TriageAction.ASK_CLARIFICATION,
    }
