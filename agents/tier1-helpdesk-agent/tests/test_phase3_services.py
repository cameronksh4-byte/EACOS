"""Phase 3: Tier 1 (context, multi-turn, approvals) and Tier 2 (flywheel, reports).

Uses the shared `env` fixture from tests/fakes.py: fake Supabase, Claude, Voyage and n8n.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Feature, get_settings
from app.gating import gated
from app.schemas.ticket import (
    CatalogItem,
    HardwareAsset,
    IncomingTicketWebhook,
    OSPlatform,
    SOPDraftDecision,
    TicketCategory,
    TicketResolvedWebhook,
    TriageAction,
    UserContext,
)
from app.services import (
    approvals,
    background,
    context_enrichment,
    conversation,
    flywheel,
    pipeline,
    reports,
    signing,
)
from tests.fakes import synthesis_output, triage_output

TIER1_ENV = {
    "SERVICE_TIER": "TIER_1_PRODUCTION",
    "PUBLIC_BASE_URL": "https://helpdesk.acme.com",
    "N8N_APPROVAL_WEBHOOK_URL": "https://n8n.acme.com/webhook/approvals",
    "N8N_OUTBOUND_SIGNING_SECRET": "outbound-secret",
    "N8N_CALLBACK_SECRET": "callback-secret",
}
TIER2_ENV = {**TIER1_ENV, "SERVICE_TIER": "TIER_2_MANAGED_OPS",
             "N8N_NOTIFY_WEBHOOK_URL": "https://n8n.acme.com/webhook/notify"}


@pytest.fixture
def tier1(monkeypatch):
    for k, v in TIER1_ENV.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


@pytest.fixture
def tier2(monkeypatch):
    for k, v in TIER2_ENV.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def ticket(**kw) -> IncomingTicketWebhook:
    base = dict(tenant_id="acme-corp", ticket_id="SLK-C1-111.1", thread_id="slack:C1:111.1",
                reporter_email="pat@acme.com", subject="VPN won't connect", body="GlobalProtect fails",
                source_system="slack")
    base.update(kw)
    return IncomingTicketWebhook(**base)


def directory_row(**kw) -> dict:
    base = {"tenant_id": "acme-corp", "email": "pat@acme.com", "department": "Finance",
            "manager_email": "boss@acme.com", "os_platform": "macos", "vip_status": False,
            "assigned_hardware": [{"model": "MacBook Pro", "os_platform": "macos", "os_version": "15.1"}],
            "recent_auth_failures": 3, "source": "manual"}
    base.update(kw)
    return base


def catalog_row(**kw) -> dict:
    base = {"tenant_id": "acme-corp", "item_key": "figma", "item_type": "software", "display_name": "Figma",
            "aliases": ["figma design"], "requires_manager_approval": True, "approver_role": "direct_manager",
            "app_owner_email": None, "provisioning_group": "sg-figma-users", "license_cost_usd": 15.0,
            "doc_id": "SOP-APP-FIGMA", "active": True}
    base.update(kw)
    return base


# =============================================================================
# Tier gating
# =============================================================================


async def test_gated_decorator_returns_default_below_tier():
    @gated(Feature.KNOWLEDGE_FLYWHEEL, default=lambda: "locked")
    async def premium():
        return "ran"

    assert await premium() == "locked"
    assert premium.__gated_feature__ is Feature.KNOWLEDGE_FLYWHEEL


async def test_tier0_runs_no_tier1_steps(env):
    env["sb"].t("tenant_directory").append(directory_row())
    env["claude"].triage = [triage_output(action="ASK_CLARIFICATION", clarifying_question="Which VPN?",
                                          cited_sop_ids=[], confidence_score=0.6)]
    result = await pipeline.process_ticket(ticket())
    await background.drain()
    assert env["sb"].t("conversation_threads") == []  # no conversation state at Tier 0
    assert "<user_context>" not in env["claude"].last_triage_text()
    assert result.action is TriageAction.ESCALATE_TIER_2  # can't ask without a capped thread


# =============================================================================
# Context enrichment
# =============================================================================


def hw(*platforms):
    return [HardwareAsset(os_platform=p) for p in platforms]


@pytest.mark.parametrize(
    "hardware, hint, expected",
    [
        (hw(OSPlatform.MACOS, OSPlatform.IOS), OSPlatform.UNKNOWN, OSPlatform.MACOS),  # only desktop
        (hw(OSPlatform.WINDOWS, OSPlatform.MACOS), OSPlatform.MACOS, OSPlatform.MACOS),  # screenshot wins
        (hw(OSPlatform.WINDOWS), OSPlatform.LINUX, OSPlatform.WINDOWS),  # hint not on user's devices
        (hw(OSPlatform.WINDOWS, OSPlatform.MACOS), OSPlatform.UNKNOWN, OSPlatform.WINDOWS),  # first listed
        ([], OSPlatform.MACOS, OSPlatform.MACOS),
        ([], OSPlatform.UNKNOWN, OSPlatform.UNKNOWN),
    ],
)
def test_os_platform_choice_is_deterministic(hardware, hint, expected):
    assert context_enrichment.choose_os_platform(hardware, hint) is expected


def test_normalise_os_and_vip_rule():
    assert context_enrichment.normalise_os("Windows 11 Enterprise") is OSPlatform.WINDOWS
    assert context_enrichment.normalise_os("macOS Sequoia") is OSPlatform.MACOS
    assert context_enrichment.normalise_os("iPadOS") is OSPlatform.IOS
    assert context_enrichment.is_vip("Chief Financial Officer")
    assert context_enrichment.is_vip("VP of Sales")
    assert not context_enrichment.is_vip("Sales Development Rep")


async def test_directory_cache_context_drives_os_filter(env, tier1):
    env["sb"].t("tenant_directory").append(directory_row())
    env["sb"].sops = [
        {"doc_id": "SOP-VPN-MAC", "title": "VPN macOS", "os_platform": "macos", "content": "mac", "similarity": 0.8},
        {"doc_id": "SOP-VPN-WIN", "title": "VPN Windows", "os_platform": "windows", "content": "win",
         "similarity": 0.8},
    ]
    env["claude"].triage = [triage_output(cited_sop_ids=["SOP-VPN-MAC"])]
    result = await pipeline.process_ticket(ticket())
    calls = [p for fn, p in env["sb"].rpc_calls if fn == "match_sop_chunks"]
    assert calls[0]["p_os_platform"] == "macos"
    text = env["claude"].last_triage_text()
    assert "<user_context>" in text and "os_platform=macos" in text and "recent_auth_failures=3" in text
    assert "SOP-VPN-WIN" not in text
    assert result.cited_sop_ids == ["SOP-VPN-MAC"]


async def test_unknown_user_applies_no_os_filter(env, tier1):
    await pipeline.process_ticket(ticket(reporter_email="new.hire@acme.com"))
    calls = [p for fn, p in env["sb"].rpc_calls if fn == "match_sop_chunks"]
    assert calls[0]["p_os_platform"] is None


def _graph_transport(fail_devices=False, user_status=200):
    seen = {"filters": []}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if "/manager" in url:
            return httpx.Response(200, json={"mail": "Boss@Acme.com"})
        if "managedDevices" in url:
            seen["filters"].append(req.url.params.get("$filter"))
            if fail_devices:
                return httpx.Response(500)
            return httpx.Response(200, json={"value": [
                {"deviceName": "PAT-OLD", "operatingSystem": "Windows", "osVersion": "10",
                 "lastSyncDateTime": "2025-01-01T00:00:00Z", "complianceState": "noncompliant"},
                {"deviceName": "PAT-MBP", "model": "MacBook Pro", "operatingSystem": "macOS", "osVersion": "15.1",
                 "lastSyncDateTime": "2026-11-10T00:00:00Z", "complianceState": "compliant"},
            ]})
        if "signIns" in url:
            return httpx.Response(200, json={"value": [{}, {}]})
        if "/users/" in url:
            if user_status != 200:
                return httpx.Response(user_status)
            return httpx.Response(200, json={"id": "1", "department": "Finance", "jobTitle": "Chief Financial Officer"})
        raise AssertionError(url)

    return httpx.MockTransport(handler), seen


async def test_graph_provider_maps_entra_and_intune(env, monkeypatch, tier1):
    monkeypatch.setenv("IDENTITY_PROVIDER", "microsoft_graph")
    for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"):
        monkeypatch.setenv(k, "x")
    get_settings.cache_clear()
    transport, seen = _graph_transport()
    monkeypatch.setattr(context_enrichment, "_http", httpx.AsyncClient(transport=transport))
    context_enrichment._live_providers.clear()
    ctx = await context_enrichment.enrich("acme-corp", "o'brien@acme.com")
    assert ctx.source == "microsoft_graph" and ctx.vip_status and ctx.department == "Finance"
    assert ctx.manager_email == "boss@acme.com" and ctx.recent_auth_failures == 2
    assert ctx.assigned_hardware[0].device_name == "PAT-MBP"  # most recently synced first
    assert ctx.os_platform is OSPlatform.MACOS  # first listed (most recent) of two desktops
    assert "o''brien@acme.com" in seen["filters"][0]  # OData literal escaped
    cached = env["sb"].t("tenant_directory")
    assert cached and cached[0]["source"] == "microsoft_graph"  # write-through


async def test_live_provider_failure_falls_back_to_cache(env, monkeypatch, tier1):
    monkeypatch.setenv("IDENTITY_PROVIDER", "okta")
    monkeypatch.setenv("OKTA_DOMAIN", "acme.okta.com")
    monkeypatch.setenv("OKTA_API_TOKEN", "x")
    get_settings.cache_clear()
    monkeypatch.setattr(context_enrichment, "_http",
                        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    context_enrichment._live_providers.clear()
    env["sb"].t("tenant_directory").append(directory_row())
    ctx = await context_enrichment.enrich("acme-corp", "pat@acme.com")
    assert ctx.source == "directory_cache" and ctx.os_platform is OSPlatform.MACOS


async def test_okta_provider_mapping(env, monkeypatch, tier1):
    monkeypatch.setenv("IDENTITY_PROVIDER", "okta")
    monkeypatch.setenv("OKTA_DOMAIN", "acme.okta.com")
    monkeypatch.setenv("OKTA_API_TOKEN", "x")
    get_settings.cache_clear()

    def handler(req):
        if req.url.path.endswith("/logs"):
            assert req.headers["Authorization"] == "SSWS x"
            return httpx.Response(200, json=[{}, {}, {}])
        return httpx.Response(200, json={"profile": {"department": "IT", "title": "Director of IT",
                                                     "managerEmail": "CIO@acme.com"}})

    monkeypatch.setattr(context_enrichment, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    context_enrichment._live_providers.clear()
    ctx = await context_enrichment.enrich("acme-corp", "pat@acme.com")
    assert ctx.source == "okta" and ctx.vip_status and ctx.recent_auth_failures == 3
    assert ctx.manager_email == "cio@acme.com"


async def test_invalid_email_never_reaches_a_provider(env, tier1):
    ctx = await context_enrichment.enrich("acme-corp", "not-an-email")
    assert ctx.source == "unavailable" and ctx.sop_platform_filter is None


# =============================================================================
# Multi-turn conversation & slot-filling
# =============================================================================


def ask(question, facts=None, confidence=0.6):
    return triage_output(action="ASK_CLARIFICATION", clarifying_question=question, cited_sop_ids=[],
                         confidence_score=confidence, known_facts=facts or [])


async def test_clarifying_questions_are_capped_at_two_then_escalate(env, tier1):
    sb, claude = env["sb"], env["claude"]
    claude.triage = [ask("Which VPN client?", [{"name": "vpn_client", "value": "GlobalProtect"}])]
    r1 = await pipeline.process_ticket(ticket())
    await background.drain()
    assert r1.action is TriageAction.ASK_CLARIFICATION and r1.clarifications_used == 1
    assert "Which VPN client?" in r1.draft_user_reply and not r1.requires_tier2_escalation
    thread = sb.t("conversation_threads")[0]
    assert thread["status"] == "awaiting_user" and thread["slots"] == {"vpn_client": "GlobalProtect"}

    claude.triage = [ask("What error do you see?")]
    r2 = await pipeline.process_ticket(ticket(body="GlobalProtect"))
    await background.drain()
    assert r2.action is TriageAction.ASK_CLARIFICATION and r2.clarifications_used == 2
    # The second turn saw the history, the known facts, and its remaining budget.
    text = claude.last_triage_text()
    assert "Clarifying questions remaining: 1" in text and "vpn_client=GlobalProtect" in text
    assert "Which VPN client?" in text

    claude.triage = [ask("And which OS?")]
    r3 = await pipeline.process_ticket(ticket(body="Error 720"))
    await background.drain()
    assert r3.action is TriageAction.ESCALATE_TIER_2 and r3.requires_tier2_escalation
    assert "And which OS?" not in r3.draft_user_reply
    assert sb.t("conversation_threads")[0]["clarification_count"] == 2
    assert sb.t("conversation_threads")[0]["status"] == "escalated"
    assert env["notified"][-1]["event"] == "thread.escalated"
    assert len(sb.t("conversation_messages")) == 6  # 3 user + 3 assistant


async def test_follow_up_skips_outage_check_and_keeps_original_issue_for_retrieval(env, tier1):
    env["claude"].triage = [ask("Which OS?")]
    await pipeline.process_ticket(ticket())
    await background.drain()
    env["sb"].burst_rows = [{"ticket_id": f"B{i}", "category": "vpn_network", "similarity": 0.95} for i in range(9)]
    env["claude"].triage = [triage_output()]
    result = await pipeline.process_ticket(ticket(subject="Windows 11", body="Windows 11"))
    assert not result.active_outage_detected
    assert "GlobalProtect fails" in env["embedded"][-1]  # original issue included


async def test_escalated_thread_gets_status_reply_without_model(env, tier1):
    env["sb"].t("conversation_threads").append({
        "tenant_id": "acme-corp", "thread_id": "slack:C1:111.1", "ticket_id": "SLK-C1-111.1",
        "status": "escalated", "clarification_count": 1, "slots": {}, "escalation_notified_at": "x"})
    result = await pipeline.process_ticket(ticket(body="any update?"))
    await background.drain()
    assert env["claude"].requests == [] and result.requires_tier2_escalation
    assert "already with" in result.draft_user_reply
    assert env["notified"][-1]["payload"]["reason"] == "requester added information"


async def test_resolved_thread_reopens_with_fresh_question_budget(env, tier1):
    env["sb"].t("conversation_threads").append({
        "tenant_id": "acme-corp", "thread_id": "slack:C1:111.1", "ticket_id": "SLK-C1-111.1",
        "status": "resolved", "clarification_count": 2, "slots": {"x": "y"}})
    env["claude"].triage = [ask("Which printer?")]
    result = await pipeline.process_ticket(ticket(body="new problem: printer"))
    assert result.action is TriageAction.ASK_CLARIFICATION and result.clarifications_used == 1


async def test_api_ticket_without_thread_cannot_ask(env, tier1):
    env["claude"].triage = [ask("Which VPN?")]
    result = await pipeline.process_ticket(ticket(thread_id=None, source_system="api"))
    assert result.action is TriageAction.ESCALATE_TIER_2


def test_merge_facts_normalises_and_caps():
    state = conversation.ThreadState("t", "th", "tk", slots={f"k{i}": "v" for i in range(12)})
    from app.schemas.ticket import KnownFact

    merged = conversation.merge_facts(state, [KnownFact(name="Device Type", value="x" * 500)])
    assert len(merged) == 12 and merged["device_type"] == "x" * 200 and "k0" not in merged


# =============================================================================
# Approvals
# =============================================================================


def test_catalog_matching_is_deterministic():
    catalog = [CatalogItem(**catalog_row()),
               CatalogItem(**catalog_row(item_key="acrobat-pro", display_name="Adobe Acrobat Pro", aliases=[])),
               CatalogItem(**catalog_row(item_key="acrobat-reader", display_name="Adobe Acrobat Reader", aliases=[]))]
    assert approvals.match_catalog("Figma", catalog).item_key == "figma"
    assert approvals.match_catalog("figma design", catalog).item_key == "figma"
    assert approvals.match_catalog("Adobe Acrobat Pro please", catalog).item_key == "acrobat-pro"
    assert approvals.match_catalog("Acrobat", catalog) is None  # ambiguous: a human decides
    assert approvals.match_catalog("Photoshop", catalog) is None


def test_approver_resolution(monkeypatch):
    user = UserContext(email="pat@acme.com", manager_email="boss@acme.com")
    assert approvals.resolve_approver(CatalogItem(**catalog_row()), user) == "boss@acme.com"
    assert approvals.resolve_approver(CatalogItem(**catalog_row()), None) is None
    owner = CatalogItem(**catalog_row(approver_role="app_owner", app_owner_email="owner@acme.com"))
    assert approvals.resolve_approver(owner, user) == "owner@acme.com"
    monkeypatch.setenv("IT_MANAGER_EMAIL", "IT-Lead@acme.com")
    get_settings.cache_clear()
    assert approvals.resolve_approver(CatalogItem(**catalog_row(approver_role="it_manager")), user) == "it-lead@acme.com"


def software_request(item="Figma"):
    return triage_output(action="AWAIT_MANAGER_APPROVAL", category="software_access", requested_item=item,
                         cited_sop_ids=[], draft_user_reply="I'll request it.")


async def test_software_request_sends_signed_approval_card(env, tier1):
    sb = env["sb"]
    sb.t("tenant_directory").append(directory_row())
    sb.t("app_catalog").append(catalog_row())
    env["claude"].triage = [software_request()]
    result = await pipeline.process_ticket(ticket(subject="Need Figma", body="Need Figma for the redesign"))
    await background.drain()
    assert result.action is TriageAction.AWAIT_MANAGER_APPROVAL and not result.requires_tier2_escalation
    approval = sb.t("approval_requests")[0]
    assert str(result.approval_id) == approval["approval_id"] and approval["approver_email"] == "boss@acme.com"
    card = env["n8n"].posts[0]
    assert card["url"] == TIER1_ENV["N8N_APPROVAL_WEBHOOK_URL"]
    assert card["body"]["event"] == "approval.requested" and card["body"]["callback_nonce"] == approval["callback_nonce"]
    assert card["body"]["callback_url"] == "https://helpdesk.acme.com/v1/callbacks/approval"
    assert signing.verify("outbound-secret", card["raw"], card["signature"], 300)
    assert "manager's approval" in result.draft_user_reply
    assert sb.t("conversation_threads")[0]["status"] == "awaiting_approval"


async def test_follow_up_while_awaiting_approval_gets_status_reply(env, tier1):
    sb = env["sb"]
    sb.t("tenant_directory").append(directory_row())
    sb.t("app_catalog").append(catalog_row())
    env["claude"].triage = [software_request()]
    await pipeline.process_ticket(ticket(body="Need Figma"))
    await background.drain()
    calls = len(env["claude"].requests)
    result = await pipeline.process_ticket(ticket(body="any news?"))
    assert len(env["claude"].requests) == calls  # no model call
    assert result.action is TriageAction.AWAIT_MANAGER_APPROVAL and "still waiting" in result.draft_user_reply


@pytest.mark.parametrize(
    "setup, note",
    [
        ({"catalog": []}, "not matched in catalog"),
        ({"catalog": [catalog_row()], "directory": [directory_row(manager_email=None)]}, "no direct_manager"),
        ({"catalog": [catalog_row()], "directory": [directory_row(manager_email="pat@acme.com")]}, "self-approve"),
        ({"catalog": [catalog_row(requires_manager_approval=False, doc_id=None)]}, "no SOP on record"),
    ],
)
async def test_approval_paths_that_escalate(env, tier1, setup, note):
    sb = env["sb"]
    sb.t("app_catalog").extend(setup.get("catalog", []))
    sb.t("tenant_directory").extend(setup.get("directory", [directory_row()]))
    env["claude"].triage = [software_request()]
    result = await pipeline.process_ticket(ticket(body="Need Figma"))
    assert result.action is TriageAction.ESCALATE_TIER_2 and result.requires_tier2_escalation
    assert note in result.internal_tech_notes
    assert sb.t("approval_requests") == []


async def test_card_delivery_failure_cancels_and_escalates(env, tier1):
    sb = env["sb"]
    sb.t("tenant_directory").append(directory_row())
    sb.t("app_catalog").append(catalog_row())
    env["n8n"].fail = True
    env["claude"].triage = [software_request()]
    result = await pipeline.process_ticket(ticket(body="Need Figma"))
    assert result.action is TriageAction.ESCALATE_TIER_2
    assert sb.t("approval_requests")[0]["status"] == "cancelled"


async def test_no_approval_item_with_sop_is_provisioned_and_resolved(env, tier1):
    sb = env["sb"]
    sb.t("tenant_directory").append(directory_row())
    sb.t("app_catalog").append(catalog_row(item_key="zoom", display_name="Zoom", aliases=[],
                                           requires_manager_approval=False, doc_id="SOP-APP-ZOOM"))
    sb.t("sop_chunks").append({"tenant_id": "acme-corp", "doc_id": "SOP-APP-ZOOM"})
    env["claude"].triage = [software_request("Zoom")]
    result = await pipeline.process_ticket(ticket(body="Can I get Zoom?"))
    await background.drain()
    assert result.action is TriageAction.RESOLVE and result.deflected
    assert result.cited_sop_ids == ["SOP-APP-ZOOM"]
    assert env["n8n"].events("provisioning.requested")[0]["provisioning_group"] == "sg-figma-users"
    assert "automatic setup" in result.draft_user_reply


def _signed(client, path, payload, secret="callback-secret"):
    body = json.dumps(payload).encode()
    return client.post(path, content=body, headers={"Content-Type": "application/json",
                                                     signing.SIGNATURE_HEADER: signing.sign(secret, body)})


@pytest.fixture
def api_t1(env, tier1):
    from app import main

    with TestClient(main.app) as client:
        yield client


def _pending_approval(sb, **kw):
    row = {"approval_id": str(uuid.uuid4()), "callback_nonce": "n" * 48, "tenant_id": "acme-corp",
           "thread_id": "slack:C1:111.1", "ticket_id": "SLK-C1-111.1", "requester_email": "pat@acme.com",
           "approver_email": "boss@acme.com", "item_type": "software", "item_key": "figma",
           "item_display_name": "Figma", "status": "pending"}
    row.update(kw)
    sb.t("approval_requests").append(row)
    sb.t("app_catalog").append(catalog_row())
    sb.t("conversation_threads").append({"tenant_id": "acme-corp", "thread_id": "slack:C1:111.1",
                                         "ticket_id": "SLK-C1-111.1", "status": "awaiting_approval"})
    return row


def _decision(row, **kw):
    base = {"approval_id": row["approval_id"], "callback_nonce": row["callback_nonce"], "tenant_id": "acme-corp",
            "decision": "approved", "decided_by_email": "boss@acme.com"}
    base.update(kw)
    return base


def test_approval_callback_resumes_provisioning(api_t1, env):
    row = _pending_approval(env["sb"])
    r = _signed(api_t1, "/v1/callbacks/approval", _decision(row))
    assert r.status_code == 200 and r.json()["provisioning_dispatched"] is True
    event = env["n8n"].events("provisioning.requested")[0]
    assert event["approval_id"] == row["approval_id"] and event["provisioning_group"] == "sg-figma-users"
    assert env["sb"].t("conversation_threads")[0]["status"] == "resolved"
    # Replay of the same click is refused.
    assert _signed(api_t1, "/v1/callbacks/approval", _decision(row)).status_code == 409


def test_approval_callback_rejects_bad_signature_wrong_approver_and_nonce(api_t1, env):
    row = _pending_approval(env["sb"])
    assert _signed(api_t1, "/v1/callbacks/approval", _decision(row), secret="wrong").status_code == 401
    assert _signed(api_t1, "/v1/callbacks/approval", _decision(row, decided_by_email="pat@acme.com")).status_code == 409
    assert _signed(api_t1, "/v1/callbacks/approval", _decision(row, callback_nonce="x" * 48)).status_code == 409
    assert _signed(api_t1, "/v1/callbacks/approval", _decision(row, tenant_id="globex")).status_code == 403
    assert env["sb"].t("approval_requests")[0]["status"] == "pending"


def test_denied_approval_closes_thread_and_notifies(api_t1, env):
    row = _pending_approval(env["sb"])
    r = _signed(api_t1, "/v1/callbacks/approval", _decision(row, decision="denied"))
    assert r.json()["status"] == "denied"
    assert env["n8n"].events("approval.denied")[0]["requester_email"] == "pat@acme.com"


def test_approved_but_provisioning_failed_escalates(api_t1, env):
    row = _pending_approval(env["sb"])
    env["n8n"].fail = True
    r = _signed(api_t1, "/v1/callbacks/approval", _decision(row))
    assert r.json()["provisioning_dispatched"] is False
    thread = env["sb"].t("conversation_threads")[0]
    assert thread["status"] == "escalated" and thread["escalation_reason"] == "provisioning_dispatch_failed"


def test_approval_callback_is_404_below_tier1(env):
    from app import main

    with TestClient(main.app) as client:
        assert _signed(client, "/v1/callbacks/approval", {"x": 1}).status_code == 404


# =============================================================================
# Tier 2: knowledge flywheel
# =============================================================================


def resolved(**kw) -> TicketResolvedWebhook:
    base = dict(tenant_id="acme-corp", ticket_id="T-77", resolved_by_email="tech@msp.com",
                subject="Outlook error 0x800CCC0E", original_description="Outlook can't send mail on my Mac",
                resolution_notes="Removed and re-added the account for pat@acme.com on mail01.corp, "
                                 "then reset the SMTP port at 10.0.0.5. Password: hunter2. Works now.",
                category=TicketCategory.UNKNOWN, system_name="Outlook")
    base.update(kw)
    return TicketResolvedWebhook(**base)


def escalated_telemetry(sb, ticket_id="T-77"):
    sb.t("ticket_telemetry").append({"tenant_id": "acme-corp", "ticket_id": ticket_id, "action": "ESCALATE_TIER_2"})


def test_scrubber_removes_identifying_and_secret_data():
    text = ("Email pat@acme.com, host mail01.corp, IP 10.0.0.5, share \\\\fs01\\finance, "
            "see https://intranet.acme.com/kb and https://support.microsoft.com/x, "
            "password: hunter2, call +1 555-123-4567, ticket T-77")
    clean, removed = flywheel.scrub(text, "T-77")
    for leaked in ("pat@acme.com", "mail01.corp", "10.0.0.5", "fs01", "intranet.acme.com", "hunter2",
                   "555-123-4567", "T-77"):
        assert leaked not in clean, leaked
    assert "https://support.microsoft.com/x" in clean  # public vendor docs kept
    assert {"email", "hostname", "ip_address", "secret", "url", "phone", "ticket"} <= set(removed)


def test_doc_id_is_deterministic_and_valid():
    a = flywheel.make_doc_id("Outlook / Exchange", "acme-corp", "T-77")
    assert a == flywheel.make_doc_id("Outlook / Exchange", "acme-corp", "T-77")
    assert a.startswith("SOP-OUTLOOK-EXCHANGE-") and a != flywheel.make_doc_id("Outlook", "acme-corp", "T-78")


async def test_flywheel_creates_sanitized_draft_for_a_gap(env, tier2):
    sb = env["sb"]
    escalated_telemetry(sb)
    sb.sops = [{"doc_id": "SOP-MAIL-001", "title": "Mail setup", "os_platform": "any", "content": "x",
                "similarity": 0.41}]
    env["claude"].synthesis = synthesis_output(
        markdown_body=synthesis_output()["markdown_body"] + "\nContact pat@acme.com if stuck.")
    outcome = await flywheel.process_resolved_ticket(resolved())
    assert outcome.draft_created and outcome.doc_id.startswith("SOP-OUTLOOK-")
    draft = sb.t("sop_drafts")[0]
    assert draft["status"] == "draft_pending_review" and draft["frontmatter"]["doc_id"] == draft["doc_id"]
    assert draft["markdown"].startswith("---\ndoc_id: ") and 'status: "draft_pending_review"' in draft["markdown"]
    assert 'os_platform: "macos"' in draft["markdown"] and "last_verified_date:" in draft["markdown"]
    assert "pat@acme.com" not in draft["markdown"]
    assert draft["nearest_doc_id"] == "SOP-MAIL-001"
    # The model saw the notes as data; raw secrets were only in its input, never in the draft.
    assert "hunter2" not in draft["markdown"]
    assert env["notified"][-1]["event"] == "sop_draft.created"
    assert len(env["claude"].synthesis_calls()) == 1


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda e: e.update(notes="Rebooted."), "too short"),
        (lambda e: e.update(no_telemetry=True), "not escalated by the agent"),
        (lambda e: e.update(similarity=0.9), "already covered by SOP-MAIL-001"),
        (lambda e: e.update(not_gap=True), "not a reusable gap"),
    ],
)
async def test_flywheel_skips(env, tier2, mutate, reason):
    opts: dict = {}
    mutate(opts)
    sb = env["sb"]
    if not opts.get("no_telemetry"):
        escalated_telemetry(sb)
    sb.sops = [{"doc_id": "SOP-MAIL-001", "title": "Mail", "os_platform": "any", "content": "x",
                "similarity": opts.get("similarity", 0.3)}]
    if opts.get("not_gap"):
        env["claude"].synthesis = synthesis_output(covers_knowledge_gap=False, reasoning="one-off fix")
    event = resolved(resolution_notes=opts["notes"]) if "notes" in opts else resolved()
    outcome = await flywheel.process_resolved_ticket(event)
    assert not outcome.draft_created and reason in outcome.reason
    assert sb.t("sop_drafts") == []


async def test_flywheel_is_idempotent_per_ticket(env, tier2):
    escalated_telemetry(env["sb"])
    env["sb"].sops = []
    first = await flywheel.process_resolved_ticket(resolved())
    second = await flywheel.process_resolved_ticket(resolved())
    assert first.draft_created and not second.draft_created and second.draft_id == first.draft_id
    assert len(env["claude"].synthesis_calls()) == 1


async def test_flywheel_disabled_below_tier2(env, tier1):
    assert await flywheel.process_resolved_ticket(resolved()) is None


async def test_one_click_ingest_puts_draft_into_rag(env, tier2):
    escalated_telemetry(env["sb"])
    env["sb"].sops = []
    outcome = await flywheel.process_resolved_ticket(resolved())
    decision = SOPDraftDecision(tenant_id="acme-corp", draft_id=outcome.draft_id, decision="approve",
                                reviewer_email="itmgr@acme.com")
    done = await flywheel.apply_review(decision)
    assert done["status"] == "ingested" and done["chunks"] >= 1
    chunks = env["sb"].t("sop_chunks")
    assert chunks and all(c["source"] == "flywheel" and c["doc_id"] == outcome.doc_id for c in chunks)
    assert all(len(c["embedding"]) == 1024 for c in chunks)
    assert "doc_id:" not in chunks[0]["content"]  # frontmatter not embedded as content
    assert env["sb"].t("sop_drafts")[0]["status"] == "ingested"
    assert await flywheel.apply_review(decision) is None  # single use


@pytest.fixture
def api_t2(env, tier2):
    from app import main

    with TestClient(main.app) as client:
        yield client


def test_ticket_resolved_endpoint_accepts_and_runs_in_background(api_t2, env):
    escalated_telemetry(env["sb"])
    env["sb"].sops = []
    r = api_t2.post("/v1/webhooks/ticket-resolved", json=resolved().model_dump(mode="json"),
                    headers={"x-webhook-secret": "test-webhook-secret"})
    assert r.status_code == 202 and r.json()["accepted"] is True


def test_sop_draft_callback_signed_and_single_use(api_t2, env):
    draft_id = str(uuid.uuid4())
    env["sb"].t("sop_drafts").append({"draft_id": draft_id, "tenant_id": "acme-corp", "status": "draft_pending_review",
                                      "doc_id": "SOP-X-1", "title": "X", "markdown": "---\ndoc_id: x\n---\n# X\n\nbody",
                                      "frontmatter": {"doc_id": "SOP-X-1"}, "os_platform": "any"})
    payload = {"tenant_id": "acme-corp", "draft_id": draft_id, "decision": "reject", "reviewer_email": "m@acme.com"}
    assert _signed(api_t2, "/v1/callbacks/sop-draft", payload, secret="nope").status_code == 401
    assert _signed(api_t2, "/v1/callbacks/sop-draft", payload).json() == {"status": "rejected"}
    assert _signed(api_t2, "/v1/callbacks/sop-draft", payload).status_code == 409


def test_tier2_routes_are_404_at_tier1(api_t1):
    h = {"x-webhook-secret": "test-webhook-secret"}
    assert api_t1.post("/v1/webhooks/ticket-resolved", json=resolved().model_dump(mode="json"), headers=h).status_code == 404
    assert api_t1.post("/v1/reports/weekly?tenant_id=acme-corp", headers=h).status_code == 404


# =============================================================================
# Tier 2: weekly executive report
# =============================================================================


async def test_weekly_report_renders_and_delivers(env, tier2):
    env["sb"].t("sop_drafts").append({"tenant_id": "acme-corp", "status": "draft_pending_review"})
    report = await reports.weekly_report("acme-corp")
    assert report.delivered and report.sop_drafts_pending_review == 1
    assert "Technician hours saved | 5.5 h" in report.markdown
    assert "1. adobe licence (4 tickets)" in report.markdown
    assert env["notified"][-1]["event"] == "report.weekly"


def test_weekly_report_endpoint(api_t2):
    r = api_t2.post("/v1/reports/weekly?tenant_id=acme-corp&deliver=false",
                    headers={"x-webhook-secret": "test-webhook-secret"})
    assert r.status_code == 200 and r.json()["delivered"] is False
    assert api_t2.post("/v1/reports/weekly?tenant_id=globex",
                       headers={"x-webhook-secret": "test-webhook-secret"}).status_code == 403


# =============================================================================
# Maintenance: housekeeping + owed notifications
# =============================================================================


async def test_maintenance_tick_runs_housekeeping_and_delivers_escalations(env, tier1):
    from app.main import maintenance_tick

    env["sb"].t("conversation_threads").append({
        "tenant_id": "acme-corp", "thread_id": "th-9", "ticket_id": "T-9", "status": "escalated",
        "escalation_reason": "approval_expired", "escalation_notified_at": None})
    await maintenance_tick(run_housekeeping=True)
    assert ("run_housekeeping", {}) in env["sb"].rpc_calls
    assert env["notified"][-1]["payload"]["reason"] == "approval_expired"
    assert env["sb"].t("conversation_threads")[0]["escalation_notified_at"] is not None
