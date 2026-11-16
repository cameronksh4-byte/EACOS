"""Regression tests for the pre-deploy line-by-line review.

One test (or a few) per confirmed finding. Each fails on the code as it was
before the fix. Grouped by the area the finding was in.
"""

from __future__ import annotations

import json
import types
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.schemas.ticket import (
    CatalogItem,
    IncomingTicketWebhook,
    ScopeVerdict,
    SOPDraftDecision,
    TicketCategory,
    TriageAction,
)
from app.services import (
    background,
    context_enrichment,
    conversation,
    flywheel,
    guardrails,
    llm_client,
    llm_router,
    notifier,
    outage,
    pipeline,
    rag,
    reports,
    signing,
    supabase_client,
    ticketing,
    vision,
)
from ingest import sync_docs
from tests.fakes import triage_output
from tests.test_phase3_services import directory_row, escalated_telemetry, resolved
from tests.test_phase3_services import ticket as thread_ticket


def api_ticket(**kw) -> IncomingTicketWebhook:
    base = dict(tenant_id="acme-corp", ticket_id="T-1", reporter_email="pat@acme.com",
                subject="VPN won't connect", body="GlobalProtect fails every time", source_system="api")
    base.update(kw)
    return IncomingTicketWebhook(**base)


def thread_row(env) -> dict:
    return env["sb"].t("conversation_threads")[0]


# =============================================================================
# Escalations always reach Tier 2
# =============================================================================


async def test_security_report_with_a_clarifying_question_still_notifies_tier2(env, tier1):
    env["claude"].triage = [triage_output(category="security_incident", action="ASK_CLARIFICATION",
                                          clarifying_question="Did you click the link?", cited_sop_ids=[])]
    result = await pipeline.process_ticket(thread_ticket())
    await background.drain()
    assert result.action is TriageAction.ASK_CLARIFICATION and result.requires_tier2_escalation
    assert [n["event"] for n in env["notified"]] == ["thread.escalated"]
    assert "security_incident" in env["notified"][0]["text"]
    row = thread_row(env)
    assert row["escalation_reason"] and row["escalation_notified_at"]  # owed, then delivered


async def test_security_notice_is_retried_while_the_thread_awaits_the_user(env, tier1, monkeypatch):
    delivered = {"ok": False}
    real = notifier.notify_tier2

    async def flaky(*a, **kw):
        return await real(*a, **kw) if delivered["ok"] else False

    monkeypatch.setattr(notifier, "notify_tier2", flaky)
    env["claude"].triage = [triage_output(category="security_incident", action="ASK_CLARIFICATION",
                                          clarifying_question="Did you click the link?", cited_sop_ids=[])]
    await pipeline.process_ticket(thread_ticket())
    await background.drain()
    assert thread_row(env)["status"] == "awaiting_user" and thread_row(env)["escalation_notified_at"] is None
    delivered["ok"] = True
    assert await conversation.retry_pending_escalations() == 1  # not only status = escalated
    assert await conversation.retry_pending_escalations() == 0


async def test_escalation_without_a_thread_is_notified_by_the_pipeline(env):
    """Tier 0 Slack, Teams (never has a thread) and API tickets."""
    env["claude"].triage = [triage_output(confidence_score=0.3)]
    result = await pipeline.process_ticket(api_ticket(source_system="teams", subject="Laptop <!channel> broken"))
    await background.drain()
    assert result.requires_tier2_escalation
    assert [n["event"] for n in env["notified"]] == ["thread.escalated"]
    assert "T-1" in env["notified"][0]["text"] and "pat@acme.com" in env["notified"][0]["text"]


async def test_escalation_survives_an_unreachable_thread_store(env, tier1, monkeypatch):
    async def down(_payload):
        raise httpx.ConnectError("supabase down")

    monkeypatch.setattr(conversation, "load", down)
    env["claude"].triage = [triage_output(confidence_score=0.2)]
    result = await pipeline.process_ticket(thread_ticket())
    await background.drain()
    assert result.requires_tier2_escalation and len(env["notified"]) == 1


async def test_escalation_survives_a_failed_turn_write(env, tier1, monkeypatch):
    async def broken(*_a, **_kw):
        raise httpx.ConnectError("write failed")

    monkeypatch.setattr(conversation, "record_turn", broken)
    env["claude"].triage = [triage_output(confidence_score=0.2)]
    await pipeline.process_ticket(thread_ticket())
    await background.drain()
    assert len(env["notified"]) == 1


async def test_second_escalation_on_a_reopened_thread_is_owed_a_new_notice(env, tier1, monkeypatch):
    env["claude"].triage = [triage_output(confidence_score=0.2)]
    await pipeline.process_ticket(thread_ticket())
    await background.drain()
    assert thread_row(env)["escalation_notified_at"]  # first escalation delivered
    await conversation.mark_resolved("acme-corp", "SLK-C1-111.1")

    async def offline(*_a, **_kw):
        return False

    real = notifier.notify_tier2
    monkeypatch.setattr(notifier, "notify_tier2", offline)
    await pipeline.process_ticket(thread_ticket(body="It's broken again"))
    await background.drain()
    row = thread_row(env)
    assert row["status"] == "escalated" and row["escalation_notified_at"] is None
    monkeypatch.setattr(notifier, "notify_tier2", real)
    assert await conversation.retry_pending_escalations() == 1


async def test_set_status_escalated_resets_the_notified_flag(env):
    env["sb"].t("conversation_threads").append({
        "tenant_id": "acme-corp", "thread_id": "th", "ticket_id": "T", "status": "awaiting_approval",
        "escalation_notified_at": "2026-01-01T00:00:00+00:00", "escalation_reason": "old"})
    await conversation.set_status("acme-corp", "th", "escalated", TriageAction.ESCALATE_TIER_2,
                                  reason="provisioning_dispatch_failed")
    row = env["sb"].t("conversation_threads")[0]
    assert row["escalation_notified_at"] is None and row["escalation_reason"] == "provisioning_dispatch_failed"


async def test_reopened_thread_starts_clean(env, tier1):
    """Old history and facts must not steer a new issue, and outage detection applies again."""
    await pipeline.process_ticket(thread_ticket())
    await background.drain()
    await conversation.mark_resolved("acme-corp", "SLK-C1-111.1")
    state = await conversation.load(thread_ticket(body="Different problem"))
    assert state.is_new and state.history == [] and not state.is_follow_up and state.slots == {}


def test_tier1_needs_a_tier2_channel_even_with_outage_detection_off(monkeypatch):
    base = dict(tenant_id="acme-corp", webhook_secret="x", supabase_url="https://x.supabase.co",
                supabase_service_key="k", voyage_api_key="v", anthropic_api_key="a",
                service_tier="TIER_1_PRODUCTION", disabled_features="outage_detection",
                public_base_url="https://h.acme.com", n8n_approval_webhook_url="https://n8n.acme.com/a",
                n8n_outbound_signing_secret="o", n8n_callback_secret="c", slack_bot_token="", slack_escalation_channel="")
    missing = Settings(_env_file=None, **base).missing_core_settings()
    assert any("SLACK_ESCALATION_CHANNEL" in m for m in missing), missing
    assert Settings(_env_file=None, **{**base, "slack_bot_token": "xoxb", "slack_escalation_channel": "C1"}
                    ).missing_core_settings() == []


# =============================================================================
# Nothing in triage may crash a ticket; policy settings are honoured
# =============================================================================


async def test_overlong_missing_sop_topic_does_not_crash(env):
    env["claude"].triage = [triage_output(cited_sop_ids=[], missing_sop_topic="x" * 500)]
    result = await pipeline.process_ticket(api_ticket())
    assert result.requires_tier2_escalation and len(result.missing_sop_topic) == 200


async def test_database_error_while_spending_a_question_escalates_instead_of_raising(env, tier1, monkeypatch):
    async def down(_state):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(conversation, "spend_clarification", down)
    env["claude"].triage = [triage_output(action="ASK_CLARIFICATION", clarifying_question="Which OS?")]
    result = await pipeline.process_ticket(thread_ticket())
    await background.drain()
    assert result.action is TriageAction.ESCALATE_TIER_2
    assert guardrails.TRIAGE_UNAVAILABLE in result.guardrail_overrides


async def test_kill_switch_applies_to_every_channel(env, monkeypatch):
    monkeypatch.setenv("DISABLED_FEATURES", "triage_and_draft,outage_detection")
    get_settings.cache_clear()
    result = await pipeline.process_ticket(api_ticket(source_system="slack"))
    assert result.action is TriageAction.ESCALATE_TIER_2 and env["claude"].requests == []


async def test_configured_confidence_threshold_above_the_floor_is_enforced(env, monkeypatch):
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.9")
    get_settings.cache_clear()
    env["claude"].triage = [triage_output(confidence_score=0.85)]
    result = await pipeline.process_ticket(api_ticket())
    assert result.action is TriageAction.ESCALATE_TIER_2
    assert "confidence_below_threshold" in result.guardrail_overrides


async def test_low_confidence_request_is_never_auto_provisioned(env, tier1):
    from tests.test_phase3_services import catalog_row

    env["sb"].t("app_catalog").append(catalog_row(requires_manager_approval=False))
    env["sb"].t("sop_chunks").append({"tenant_id": "acme-corp", "doc_id": "SOP-APP-FIGMA"})
    env["claude"].triage = [triage_output(category="software_access", requested_item="Figma", confidence_score=0.5,
                                          cited_sop_ids=[])] * 2
    result = await pipeline.process_ticket(thread_ticket(body="Can I get Figma?"))
    await background.drain()
    assert result.action is TriageAction.ESCALATE_TIER_2
    assert env["n8n"].events("provisioning.requested") == []  # nothing dispatched before the escalation


# =============================================================================
# Model calls: refusals, fallback cost, settlement
# =============================================================================


def _result(output=None, refused=False, error=None, model="m"):
    return llm_client.StructuredResult(model=model, output=output, refused=refused, error=error)


async def test_second_opinion_refusal_keeps_the_first_security_triage(env, monkeypatch):
    from app.schemas.ticket import LLMTriageOutput

    first = LLMTriageOutput(**triage_output(category="security_incident", severity="P2_HIGH"))
    replies = [_result(first, model="claude-haiku-5-5"), _result(refused=True, model="claude-sonnet-5-5")]

    async def fake_call(**_kw):
        return replies.pop(0)

    monkeypatch.setattr(llm_client, "structured_call", fake_call)
    decision = await llm_router.route_ticket(api_ticket(), [], ledger=llm_client.CostLedger())
    assert decision.output.category is TicketCategory.SECURITY_INCIDENT
    assert decision.output.scope is ScopeVerdict.IN_SCOPE  # not turned into a polite decline


async def test_refusal_with_no_usable_answer_goes_to_a_human(env, monkeypatch):
    replies = [_result(error="invalid_output"), _result(refused=True)]

    async def fake_call(**_kw):
        return replies.pop(0)

    monkeypatch.setattr(llm_client, "structured_call", fake_call)
    with pytest.raises(llm_router.LLMUnavailableError):
        await llm_router.route_ticket(api_ticket(), [], ledger=llm_client.CostLedger())


def test_fallback_served_response_bills_every_attempt():
    usage = types.SimpleNamespace(
        input_tokens=3000, output_tokens=400, cache_read_input_tokens=0, cache_creation_input_tokens=0,
        iterations=[
            {"type": "message", "input_tokens": 3000, "output_tokens": 50,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},  # declined hop, requested model
            {"type": "fallback_message", "model": "claude-sonnet-5", "input_tokens": 3000, "output_tokens": 400,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        ])
    cost = llm_client.cost_from_usage("claude-sonnet-5", usage, requested_model="claude-sonnet-5-5")
    assert cost == pytest.approx((3000 * 2 + 50 * 10 + 3000 * 2 + 400 * 10) / 1e6)
    plain = types.SimpleNamespace(input_tokens=3000, output_tokens=400, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0, iterations=None)
    assert llm_client.cost_from_usage("claude-sonnet-5-5", plain) == pytest.approx((3000 * 2 + 400 * 10) / 1e6)


def test_reservation_covers_every_fallback_hop_at_the_highest_price():
    content = [{"type": "text", "text": "x" * 3000}]
    one = llm_client.worst_case_cost("claude-sonnet-5-5", 8192, "sys", content)
    assert llm_client.worst_case_cost("claude-sonnet-5-5", 8192, "sys", content, fallbacks=True) == pytest.approx(
        one * llm_client.FALLBACK_MAX_HOPS, rel=1e-4)


async def test_settle_is_retried(env, monkeypatch):
    monkeypatch.setattr(llm_client, "SETTLE_RETRY_DELAY_SECONDS", 0)
    attempts = []

    async def flaky(fn, params):
        attempts.append(fn)
        if len(attempts) < 3:
            raise httpx.ConnectError("blip")
        return True

    monkeypatch.setattr(supabase_client, "rpc", flaky)
    await llm_client.settle("acme-corp", "res-1", "claude-haiku-5-5", None, 0.001)
    assert attempts == ["settle_llm_budget"] * 3


def test_partial_price_override_keeps_the_defaults_and_unknown_models_cost_the_maximum(monkeypatch):
    monkeypatch.setenv("MODEL_PRICES_PER_MTOK", json.dumps({"claude-haiku-5-5": [9.0, 0.5]}))
    get_settings.cache_clear()
    s = get_settings()
    assert s.price_for("claude-sonnet-5-5") == (2.0, 10.0)  # default survived the override
    assert s.price_for("some-future-model") == (9.0, 10.0)  # highest input AND highest output


async def test_void_rpc_with_204_returns_none(monkeypatch):
    client = httpx.AsyncClient(base_url="https://x.supabase.co/rest/v1",
                               transport=httpx.MockTransport(lambda _r: httpx.Response(204)))
    monkeypatch.setattr(supabase_client, "_client", client)
    assert await supabase_client.rpc("run_housekeeping", {}) is None


# =============================================================================
# Screenshot fetching
# =============================================================================


def test_cgnat_and_other_non_global_addresses_are_not_public():
    assert not vision._is_public_ip("100.64.0.1")  # carrier-grade NAT
    assert not vision._is_public_ip("::ffff:10.0.0.1") and not vision._is_public_ip("192.0.2.1")
    assert vision._is_public_ip("8.8.8.8")


def test_credentials_only_go_to_the_exact_host(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://acme.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "bot@acme.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "jira-token")
    get_settings.cache_clear()
    assert vision._auth_headers("files.slack.com")["Authorization"] == "Bearer xoxb-test"
    assert vision._auth_headers("evil.slack.com") == {} and vision._auth_headers("slack.com") == {}
    assert vision._auth_headers("acme.atlassian.net")["Authorization"].startswith("Basic ")
    assert vision._auth_headers("attacker.atlassian.net") == {}


async def test_fetch_never_raises_and_asks_jira_for_the_bytes_directly(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://acme.atlassian.net")
    get_settings.cache_clear()
    assert await vision.fetch_image("https://files.slack.com:99999/x.png") is None
    assert await vision.fetch_image("https://éé" + "a" * 80 + ".slack.com/x.png") is None

    async def public(_host):
        return ["93.184.216.34"]

    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    monkeypatch.setattr(vision, "vetted_addresses", public)
    monkeypatch.setattr(vision, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    image = await vision.fetch_image("https://acme.atlassian.net/rest/api/3/attachment/content/10001")
    assert image is not None and seen[0].url.params["redirect"] == "false"


# =============================================================================
# Outage detection
# =============================================================================


async def test_one_person_cannot_open_an_outage(env):
    env["sb"].burst_rows = [{"ticket_id": f"B{i}", "summary": "VPN", "category": "vpn_network",
                             "parent_incident_id": None, "similarity": 0.95, "created_at": None,
                             "reporter_email": "pat@acme.com"} for i in range(8)]
    result = await pipeline.process_ticket(api_ticket())
    assert not result.active_outage_detected
    assert not [c for c in env["sb"].rpc_calls if c[0] == "register_outage_cluster"]


async def test_outage_stores_reporter_and_category(env):
    env["sb"].burst_rows = [{"ticket_id": f"B{i}", "summary": "VPN", "category": "vpn_network",
                             "parent_incident_id": None, "similarity": 0.95, "created_at": None,
                             "reporter_email": f"u{i}@acme.com"} for i in range(5)]
    await pipeline.process_ticket(api_ticket())
    await background.drain()
    assert env["sb"].recent["T-1"]["reporter_email"] == "pat@acme.com"
    registered = next(p for fn, p in env["sb"].rpc_calls if fn == "register_outage_cluster")
    assert registered["p_category"] == "vpn_network"


# =============================================================================
# Slack, Teams, Jira handlers and signatures
# =============================================================================


@pytest.fixture
def slack(monkeypatch, env):
    posts, tickets = [], []

    async def fake_api(method, payload):
        posts.append(payload)
        return {"ok": True}

    async def fake_email(_user):
        return "pat@acme.com"

    async def fake_process(ticket):
        tickets.append(ticket)
        return guardrails.unavailable_result(ticket, get_settings(), "test").model_copy(
            update={"draft_user_reply": "Try <!channel> & <https://evil.example|this>"})

    monkeypatch.setattr(ticketing, "_slack_api", fake_api)
    monkeypatch.setattr(ticketing, "_slack_user_email", fake_email)
    monkeypatch.setattr(ticketing, "process_ticket", fake_process)
    monkeypatch.setattr(ticketing, "seen_events", ticketing._SeenEvents())
    return types.SimpleNamespace(posts=posts, tickets=tickets)


async def test_slack_mention_in_a_known_thread_is_processed_once(slack, env, tier1):
    env["sb"].t("conversation_threads").append({"tenant_id": "acme-corp", "thread_id": "slack:C1:100.1"})
    mention = {"type": "app_mention", "channel": "C1", "ts": "200.2", "thread_ts": "100.1", "user": "U1",
               "text": "<@UBOT> still broken"}
    message = {**mention, "type": "message", "channel_type": "channel"}
    # Whichever worker gets the plain message event skips it (it mentions the bot)...
    assert ticketing.slack_thread_reply(message, "UBOT") is None
    # ...and the same message is one turn even when both events reach one worker.
    await ticketing.handle_slack_event(mention, "UBOT")
    await ticketing.handle_slack_event(message, "")
    assert len(slack.tickets) == 1 and len(slack.posts) == 1


async def test_slack_reply_is_escaped_and_escalation_is_left_to_the_pipeline(slack):
    await ticketing.handle_slack_event({"type": "app_mention", "channel": "C1", "ts": "1.1", "user": "U1",
                                        "text": "<@UBOT> help"})
    assert len(slack.posts) == 1  # the reply only; Tier 2 is notified by the pipeline, once
    text = slack.posts[0]["text"]
    assert "<!channel>" not in text and "&lt;!channel&gt;" in text and "&amp;" in text


def test_slack_screenshot_messages_are_not_dropped():
    event = {"type": "app_mention", "subtype": "file_share", "channel": "C1", "ts": "1.1", "user": "U1",
             "text": "<@UBOT> see screenshot"}
    assert ticketing.slack_event_to_ticket(event) == ("C1", "1.1", "see screenshot")
    assert ticketing.slack_event_to_ticket({**event, "subtype": "message_changed"}) is None


def test_signature_checks_reject_odd_input_instead_of_raising():
    assert ticketing.verify_slack_signature("s", "9" * 400, b"{}", "v0=abc") is False  # overflow
    assert ticketing.verify_slack_signature("s", "1700000000", b"{}", "v0=é", now=1700000000) is False
    assert ticketing.verify_jira_signature("s", b"{}", "sha256=é") is False
    assert ticketing.verify_teams_signature("c2VjcmV0", b"{}", "HMAC é") is False
    assert ticketing.verify_teams_signature("not base64!!", b"{}", "HMAC abc") is False
    assert signing.verify("s", b"{}", "t=1700000000,v1=é", 300, now=1700000000) is False
    assert signing.verify("s", b"{}", f"t={'9' * 400},v1=ab", 300) is False


async def test_teams_text_is_unescaped_and_sender_resolved(env, monkeypatch):
    seen = []

    async def fake_resolve(object_id):
        return "pat@acme.com" if object_id == "11111111-2222-3333-4444-555555555555" else None

    async def fake_process(ticket):
        seen.append(ticket)
        return guardrails.unavailable_result(ticket, get_settings(), "test")

    monkeypatch.setattr(context_enrichment, "resolve_teams_email", fake_resolve)
    monkeypatch.setattr(ticketing, "process_ticket", fake_process)
    await ticketing.process_teams_activity({
        "id": "1", "text": "<at>Help Desk</at> R&amp;D share &lt;drive&gt;&nbsp;missing",
        "from": {"aadObjectId": "11111111-2222-3333-4444-555555555555"}})
    assert seen[0].reporter_email == "pat@acme.com" and seen[0].body == "R&D share <drive> missing"
    assert ticketing.teams_activity_to_ticket({"text": "hi there", "from": "weird"}).reporter_email.endswith("@teams.invalid")


def test_malformed_signed_bodies_are_400_and_docs_are_hidden(env):
    import hashlib
    import hmac
    import time

    from app import main

    with TestClient(main.app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path
        for body in (b"not json", b"[1, 2]", b"\xff\xfe"):
            ts = str(int(time.time()))
            sig = "v0=" + hmac.new(b"slack-secret", f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
            r = client.post("/webhooks/slack", content=body,
                            headers={"x-slack-request-timestamp": ts, "x-slack-signature": sig})
            assert r.status_code == 400, (body, r.status_code)
            sig = "sha256=" + hmac.new(b"jira-secret", body, hashlib.sha256).hexdigest()
            assert client.post("/webhooks/jira", content=body, headers={"x-hub-signature": sig}).status_code == 400
        body = json.dumps({"webhookEvent": "jira:issue_created", "issue": "not-an-object"}).encode()
        sig = "sha256=" + hmac.new(b"jira-secret", body, hashlib.sha256).hexdigest()
        assert client.post("/webhooks/jira", content=body, headers={"x-hub-signature": sig}).json()["ignored"]


# =============================================================================
# Identity context
# =============================================================================


@pytest.mark.parametrize("title, vip", [
    ("VP, Sales", True), ("Chief of Staff", True), ("Senior Vice President", True), ("ciso", True),
    ("Art Director", True), ("Chiefly Engineer", False), ("Supervisor", False), ("VPN Engineer", False),
    ("Directorate Analyst", False), ("", False), (None, False),
])
def test_vip_titles_match_whole_words_case_insensitively(title, vip, monkeypatch):
    monkeypatch.setenv("VIP_JOB_TITLE_KEYWORDS", "Chief,VP,vice president,Director,CISO")  # mixed case on purpose
    get_settings.cache_clear()
    assert context_enrichment.is_vip(title) is vip


def _okta(monkeypatch, handler):
    monkeypatch.setenv("IDENTITY_PROVIDER", "okta")
    monkeypatch.setenv("OKTA_DOMAIN", "acme.okta.com")
    monkeypatch.setenv("OKTA_API_TOKEN", "x")
    get_settings.cache_clear()
    monkeypatch.setattr(context_enrichment, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    context_enrichment._live_providers.clear()


async def test_okta_lookup_adds_to_the_directory_row_instead_of_blanking_it(env, monkeypatch, tier1):
    env["sb"].t("tenant_directory").append(directory_row())  # operator-loaded: hardware + manager

    def handler(req):
        if req.url.path.endswith("/logs"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"profile": {"department": "Finance", "title": "Analyst"}})

    _okta(monkeypatch, handler)
    ctx = await context_enrichment.enrich("acme-corp", "pat@acme.com")
    assert ctx.source == "okta" and ctx.manager_email == "boss@acme.com"
    assert ctx.assigned_hardware and ctx.os_platform.value == "macos"
    row = env["sb"].t("tenant_directory")[0]
    assert row["manager_email"] == "boss@acme.com" and row["assigned_hardware"]  # not clobbered


async def test_okta_manager_id_is_resolved_to_an_email(env, monkeypatch, tier1):
    def handler(req):
        if req.url.path.endswith("/logs"):
            return httpx.Response(200, json=[])
        if req.url.path.endswith("/00uBOSS"):
            return httpx.Response(200, json={"profile": {"email": "Boss@acme.com"}})
        return httpx.Response(200, json={"profile": {"managerId": "00uBOSS"}})

    _okta(monkeypatch, handler)
    ctx = await context_enrichment.enrich("acme-corp", "pat@acme.com")
    assert ctx.manager_email == "boss@acme.com"


async def test_stale_live_cache_row_does_not_pick_the_approver(env, monkeypatch, tier1):
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    env["sb"].t("tenant_directory").append(directory_row(source="okta", synced_at=old, manager_email="left@acme.com"))
    _okta(monkeypatch, lambda _req: httpx.Response(503))
    ctx = await context_enrichment.enrich("acme-corp", "pat@acme.com")
    assert ctx.manager_email is None and ctx.assigned_hardware  # device info is still useful
    # A row the operator loaded doesn't expire.
    env["sb"].t("tenant_directory")[0].update(source="manual")
    assert (await context_enrichment.enrich("acme-corp", "pat@acme.com")).manager_email == "left@acme.com"


def test_identifiers_are_one_path_segment():
    assert context_enrichment._path("a/b?c#d%e@acme.com") == "a%2Fb%3Fc%23d%25e@acme.com"


# =============================================================================
# Knowledge flywheel, ingest, reports
# =============================================================================


@pytest.mark.parametrize("text, gone, kept", [
    ("Call (956) 555-0142 or 956-555-0142", ["555-0142"], []),
    ("The **Password:** Hunter2! then **Password**: Abc123", ["Hunter2", "Abc123"], []),
    ("the password is Hunter2! and password was 'letmein'", ["Hunter2", "letmein"], []),
    ("A password is required to continue", [], ["password is required"]),
    ("key AKIAIOSFODNN7EXAMPLE leaked", ["AKIAIOSFODNN7EXAMPLE"], []),
    ("ping fe80::1ff:fe23:4567:890a and 2001:db8::1", ["fe80::1ff", "2001:db8::1"], []),
    ("see https://microsoft.com.evil.example/login", ["evil.example"], []),
    ("see https://support.microsoft.com/help/123", [], ["https://support.microsoft.com/help/123"]),
    ("Install GlobalProtect 6.2.1.132 or version 10.20.30.40", [], ["6.2.1.132", "10.20.30.40"]),
    ("server at 10.20.30.40 and 172.16.5.9", ["10.20.30.40", "172.16.5.9"], []),
    ("Error 2147942405 at 12:30:45", [], ["2147942405", "12:30:45"]),
])
def test_scrubber_catches_secrets_without_mangling_versions(text, gone, kept):
    cleaned, _ = flywheel.scrub(text, "T-1")
    for fragment in gone:
        assert fragment not in cleaned, cleaned
    for fragment in kept:
        assert fragment in cleaned, cleaned


async def _pending_draft(env):
    escalated_telemetry(env["sb"])
    env["sb"].sops = []
    outcome = await flywheel.process_resolved_ticket(resolved())
    return SOPDraftDecision(tenant_id="acme-corp", draft_id=outcome.draft_id, decision="approve",
                            reviewer_email="itmgr@acme.com")


async def test_failed_embedding_leaves_the_draft_reviewable(env, tier2, monkeypatch):
    decision = await _pending_draft(env)
    good = rag.embed_documents

    async def down(_texts, batch=64):
        raise httpx.ConnectError("voyage down")

    monkeypatch.setattr(rag, "embed_documents", down)
    with pytest.raises(httpx.ConnectError):
        await flywheel.apply_review(decision)
    assert env["sb"].t("sop_drafts")[0]["status"] == "draft_pending_review"
    monkeypatch.setattr(rag, "embed_documents", good)
    assert (await flywheel.apply_review(decision))["status"] == "ingested"


async def test_approved_draft_whose_ingest_failed_can_be_finished(env, tier2, monkeypatch):
    decision = await _pending_draft(env)
    good = supabase_client.upsert

    async def down(table, rows, on_conflict):
        raise httpx.ConnectError("supabase down")

    monkeypatch.setattr(supabase_client, "upsert", down)
    with pytest.raises(httpx.ConnectError):
        await flywheel.apply_review(decision)
    assert env["sb"].t("sop_drafts")[0]["status"] == "approved"
    monkeypatch.setattr(supabase_client, "upsert", good)
    assert (await flywheel.apply_review(decision))["status"] == "ingested"  # used to be stranded forever
    assert await flywheel.apply_review(decision) is None  # still single-use


class _FakeSupabaseRest:
    """Enough of PostgREST for sync_docs: paged GET, upsert, delete."""

    def __init__(self, rows, page_cap=1000):
        self.rows, self.page_cap = rows, page_cap
        self.upserted, self.deleted, self.embedded = [], [], 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.voyageai.com":
            n = len(json.loads(req.content)["input"])
            self.embedded += n
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [0.0] * 4} for i in range(n)]})
        if req.method == "GET":
            offset, limit = int(req.url.params.get("offset", 0)), int(req.url.params["limit"])
            assert req.url.params["order"] == "id.asc"
            return httpx.Response(200, json=self.rows[offset: offset + min(limit, self.page_cap)])
        if req.method == "POST":
            self.upserted += json.loads(req.content)
            return httpx.Response(201)
        if req.method == "DELETE":
            assert req.url.params["source"] == "eq.ingest"
            self.deleted += [i.strip('"') for i in req.url.params["id"][4:-1].split(",")]
            return httpx.Response(204)
        raise AssertionError(req)


def _run_sync(monkeypatch, tmp_path, rest, *args):
    real = httpx.Client
    monkeypatch.setattr(sync_docs.httpx, "Client", lambda **_kw: real(transport=httpx.MockTransport(rest)))
    return sync_docs.main(["--tenant", "acme-corp", "--docs", str(tmp_path), *args])


def test_prune_never_deletes_flywheel_sops(monkeypatch, tmp_path):
    (tmp_path / "vpn.md").write_text("---\ndoc_id: SOP-NET-001\ntitle: VPN\n---\n# VPN\n\n1. Reconnect.\n")
    rest = _FakeSupabaseRest([
        {"id": "flywheel-row", "content_hash": "h1", "source": "flywheel"},
        {"id": "old-ingest-row", "content_hash": "h2", "source": "ingest"},
    ])
    assert _run_sync(monkeypatch, tmp_path, rest, "--prune") == 0
    assert rest.deleted == ["old-ingest-row"]


def test_sync_pages_through_all_existing_chunks(monkeypatch, tmp_path):
    (tmp_path / "vpn.md").write_text("---\ndoc_id: SOP-NET-001\ntitle: VPN\n---\n# VPN\n\n1. Reconnect.\n")
    chunk = sync_docs.load_chunks(tmp_path)[0]
    rows = [{"id": f"00-filler-{i:04d}", "content_hash": "x", "source": "flywheel"} for i in range(1200)]
    rows.append({"id": chunk.row_id("acme-corp"), "content_hash": chunk.content_hash, "source": "ingest"})
    rows.sort(key=lambda r: r["id"])
    rest = _FakeSupabaseRest(rows)
    assert _run_sync(monkeypatch, tmp_path, rest) == 0
    assert rest.embedded == 0 and rest.upserted == []  # the unchanged chunk beyond row 1000 was seen


def test_front_matter_survives_a_utf8_bom(tmp_path):
    path = tmp_path / "bom.md"
    path.write_bytes("﻿---\ndoc_id: SOP-BOM-001\ntitle: With BOM\n---\n# With BOM\n\nBody.\n".encode())
    meta, _ = sync_docs.parse_document(path)
    assert meta.doc_id == "SOP-BOM-001" and meta.title == "With BOM"


def test_catalog_keys_follow_the_database_rule():
    with pytest.raises(ValueError):
        CatalogItem(tenant_id="acme-corp", item_key="Bad Key!", item_type="software", display_name="X")


async def test_weekly_report_queries_the_previous_week_as_its_own_window(env, tier2):
    await reports.weekly_report("acme-corp", deliver=False)
    windows = [(datetime.fromisoformat(p["p_since"]), datetime.fromisoformat(p["p_until"]))
               for fn, p in env["sb"].rpc_calls if fn == "tenant_roi_summary"]
    assert len(windows) == 2
    (this_since, this_until), (prev_since, prev_until) = windows
    assert this_until - this_since == timedelta(days=7) and prev_until - prev_since == timedelta(days=7)
    assert prev_until == this_since  # adjacent, non-overlapping weeks (no subtraction of non-additive counts)


async def test_outage_notify_escapes_slack_control_characters(monkeypatch):
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(notifier, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await notifier.notify_tier2("thread.escalated", "acme-corp", {}, "Subject: <!channel> & <@U1>")
    assert sent[0]["text"] == "Subject: &lt;!channel&gt; &amp; &lt;@U1&gt;"


def test_llm_timeout_cannot_outlive_a_budget_reservation():
    with pytest.raises(ValueError):
        Settings(_env_file=None, llm_timeout_seconds=600)


async def test_maintenance_loop_runs_only_for_the_lease_holder(env, monkeypatch):
    from app import main

    ticks, sleeps = [], []

    async def fake_tick(run_housekeeping):
        ticks.append(run_housekeeping)

    async def fake_sleep(_seconds):
        sleeps.append(1)
        if len(sleeps) > 3:
            raise RuntimeError("stop")

    leases = iter([True, False, True])

    async def fake_rpc(fn, params):
        assert fn == "try_acquire_maintenance_lease" and params["p_ttl_seconds"] < main.NOTIFICATION_RETRY_SECONDS
        return next(leases)

    monkeypatch.setattr(main, "maintenance_tick", fake_tick)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(supabase_client, "rpc", fake_rpc)
    with pytest.raises(RuntimeError):
        await main._notification_retry_loop()
    assert len(ticks) == 2  # the tick another worker held was skipped


async def test_outage_retry_helper_is_exported():
    assert callable(outage.retry_pending_notifications)
