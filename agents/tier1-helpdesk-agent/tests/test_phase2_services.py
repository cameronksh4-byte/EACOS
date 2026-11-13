"""Phase 2: Tier 0 services and pipeline, against in-memory fakes (tests/fakes.py)."""

from __future__ import annotations

import json
import uuid
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Feature, ServiceTier, get_settings
from app.schemas.ticket import (
    IncomingTicketWebhook,
    LLMVisionExtraction,
    OSPlatform,
    ScopeVerdict,
    TicketCategory,
    TriageAction,
)
from app.services import (
    background,
    llm_client,
    llm_router,
    notifier,
    outage,
    pipeline,
    rag,
    signing,
    supabase_client,
    telemetry,
    ticketing,
    vision,
)
from tests.fakes import PNG, triage_output, vision_output



def ticket(**kw):
    base = dict(tenant_id="acme-corp", ticket_id="T-1", reporter_email="pat@acme.com",
                subject="VPN won't connect", body="GlobalProtect fails every time", source_system="api")
    base.update(kw)
    return IncomingTicketWebhook(**base)


def use_settings(monkeypatch, **env_overrides):
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Happy path, budget (guardrail #3), trial
# ---------------------------------------------------------------------------


async def test_grounded_ticket_resolves_and_reserves_budget_before_each_call(env):
    result = await pipeline.process_ticket(ticket())
    await background.drain()
    assert result.action is TriageAction.RESOLVE and result.deflected
    assert result.cited_sop_ids == ["SOP-NET-001"]
    sb = env["sb"]
    assert len(sb.reservations) == len(env["claude"].requests) == 1
    assert sb.settled[0]["p_cost_usd"] > 0
    assert sb.reservations[0]["p_cap_usd"] == get_settings().monthly_token_budget_usd
    assert sb.telemetry[0]["deflected_bool"] is True and sb.telemetry[0]["estimated_minutes_saved"] == 15


async def test_budget_cap_reached_means_no_model_call(env):
    env["sb"].cap_ok = False
    result = await pipeline.process_ticket(ticket())
    assert env["claude"].requests == []
    assert result.requires_tier2_escalation and "budget" in result.internal_tech_notes


async def test_unreachable_ledger_fails_closed(env):
    env["sb"].ledger_down = True
    result = await pipeline.process_ticket(ticket())
    assert env["claude"].requests == []
    assert result.requires_tier2_escalation and "couldn't be verified" in result.internal_tech_notes


async def test_budget_exhausted_before_second_opinion_keeps_first_answer(env, monkeypatch):
    env["claude"].triage = [triage_output(confidence_score=0.6)]
    real_reserve = llm_client.reserve
    calls = {"n": 0}

    async def reserve_once(*a, **k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise llm_client.BudgetExceededError("cap")
        return await real_reserve(*a, **k)

    monkeypatch.setattr(llm_client, "reserve", reserve_once)
    result = await pipeline.process_ticket(ticket())
    assert len(env["claude"].triage_calls()) == 1
    assert result.requires_tier2_escalation  # low confidence still escalates


async def test_expired_trial_hands_tickets_to_humans(env, monkeypatch):
    use_settings(monkeypatch, TRIAL_EXPIRES_ON="2020-01-01")
    result = await pipeline.process_ticket(ticket())
    assert env["claude"].requests == [] and env["embedded"] == []
    assert result.requires_tier2_escalation and "trial" in result.internal_tech_notes


def test_trial_not_expired_on_its_last_day():
    s = get_settings().model_copy(update={"trial_expires_on": date(2026, 12, 1)})
    assert not s.trial_expired(today=date(2026, 12, 1))


# ---------------------------------------------------------------------------
# Tier 0 has no multi-turn: a model clarification request escalates
# ---------------------------------------------------------------------------


async def test_tier0_clarification_request_escalates_with_handoff(env):
    env["claude"].triage = [triage_output(action="ASK_CLARIFICATION", clarifying_question="Which VPN?",
                                           cited_sop_ids=[], confidence_score=0.85)]
    result = await pipeline.process_ticket(ticket())
    assert result.action is TriageAction.ESCALATE_TIER_2
    assert "clarification_not_backed_by_capped_thread" in result.guardrail_overrides
    assert "Which VPN?" not in result.draft_user_reply


def test_system_prompt_matches_tier_capabilities(monkeypatch):
    tier0 = llm_router.system_prompt(get_settings())
    assert "Clarifying questions are not available" in tier0
    use_settings(monkeypatch, SERVICE_TIER="TIER_1_PRODUCTION")
    tier1 = llm_router.system_prompt(get_settings())
    assert "ASK_CLARIFICATION" in tier1 and "AWAIT_MANAGER_APPROVAL" in tier1


# ---------------------------------------------------------------------------
# Outage burst detection
# ---------------------------------------------------------------------------


def _burst(n, category="vpn_network"):
    return [{"ticket_id": f"B{i}", "summary": "VPN", "category": category, "parent_incident_id": None,
             "similarity": 0.93, "created_at": None} for i in range(n)]


async def test_outage_burst_broadcasts_without_any_model_call(env):
    env["sb"].burst_rows = _burst(5)
    result = await pipeline.process_ticket(ticket(attachment_urls=["https://files.slack.com/x.png"]))
    await background.drain()
    assert result.active_outage_detected and str(result.parent_incident_id) == env["sb"].incident["incident_id"]
    assert env["claude"].requests == []  # no vision, no triage tokens
    assert not result.requires_tier2_escalation and not result.deflected
    assert "several reports like yours" in result.draft_user_reply
    assert "B1" not in result.draft_user_reply  # never echoes other users' tickets
    # Exemption control: the parent incident is escalated, and delivery recorded.
    assert env["notified"][0]["event"] == "outage.detected"
    assert env["sb"].marked == [env["sb"].incident["incident_id"]]
    assert env["sb"].telemetry[0]["outage_detected"] is True and env["sb"].telemetry[0]["estimated_minutes_saved"] == 0


async def test_below_threshold_is_normal_triage(env):
    env["sb"].burst_rows = _burst(4)
    result = await pipeline.process_ticket(ticket())
    assert not result.active_outage_detected and len(env["claude"].triage_calls()) == 1


async def test_similar_untriaged_cluster_is_not_an_outage(env):
    env["sb"].burst_rows = _burst(6, category=None)  # e.g. five payroll questions
    result = await pipeline.process_ticket(ticket())
    assert not result.active_outage_detected


async def test_phishing_wave_broadcast_still_escalates(env):
    env["sb"].burst_rows = _burst(4) + _burst(1, "security_incident")
    result = await pipeline.process_ticket(ticket(subject="Weird email", body="asks for my password"))
    assert result.active_outage_detected and result.category is TicketCategory.SECURITY_INCIDENT
    assert result.requires_tier2_escalation
    assert "security team" in result.draft_user_reply


async def test_outage_notification_not_delivered_is_left_pending(env, monkeypatch):
    async def fail(*_a, **_k):
        return False

    monkeypatch.setattr(notifier, "notify_tier2", fail)
    env["sb"].burst_rows = _burst(5)
    await pipeline.process_ticket(ticket())
    await background.drain()
    assert env["sb"].marked == []  # retry loop will pick it up


async def test_outage_detection_kill_switch(env, monkeypatch):
    use_settings(monkeypatch, DISABLED_FEATURES="outage_detection")
    env["sb"].burst_rows = _burst(9)
    result = await pipeline.process_ticket(ticket())
    assert not result.active_outage_detected and env["sb"].recent == {}


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------


async def test_screenshot_findings_feed_retrieval_and_triage(env, monkeypatch):
    async def fake_fetch(url):
        return vision.FetchedImage("image/png", PNG)

    monkeypatch.setattr(vision, "fetch_image", fake_fetch)
    result = await pipeline.process_ticket(ticket(attachment_urls=["https://files.slack.com/a.png"]))
    assert len(env["claude"].vision_calls()) == 1
    image_block = env["claude"].vision_calls()[0]["messages"][0]["content"][1]
    assert image_block["type"] == "image" and image_block["source"]["media_type"] == "image/png"
    # Re-embedded with the exact error code for retrieval...
    assert len(env["embedded"]) == 2 and "0x80070005" in env["embedded"][1]
    # ...and passed to triage as untrusted, escaped data.
    triage_text = env["claude"].triage_calls()[0]["messages"][0]["content"][0]["text"]
    assert "<screenshot_findings>" in triage_text and "0x80070005" in triage_text
    assert result.action is TriageAction.RESOLVE
    assert len(env["sb"].reservations) == 2  # vision + triage, each budget-gated


async def test_sensitive_screenshot_text_is_dropped(env, monkeypatch):
    async def fake_fetch(url):
        return vision.FetchedImage("image/png", PNG)

    monkeypatch.setattr(vision, "fetch_image", fake_fetch)
    env["claude"].vision = {"screenshots": [vision_output(dialog_text="pw: hunter2", contains_sensitive_data=True)]}
    await pipeline.process_ticket(ticket(attachment_urls=["https://files.slack.com/a.png"]))
    assert all("hunter2" not in t for t in env["embedded"])
    assert "hunter2" not in env["claude"].triage_calls()[0]["messages"][0]["content"][0]["text"]


async def test_vision_kill_switch_skips_fetching(env, monkeypatch):
    use_settings(monkeypatch, DISABLED_FEATURES="screenshot_vision")

    async def must_not_fetch(url):
        raise AssertionError("fetched while disabled")

    monkeypatch.setattr(vision, "fetch_image", must_not_fetch)
    await pipeline.process_ticket(ticket(attachment_urls=["https://files.slack.com/a.png"]))
    assert env["claude"].vision_calls() == []


def test_image_sniffing_uses_magic_bytes():
    assert vision.sniff_image_type(PNG) == "image/png"
    assert vision.sniff_image_type(b"\xff\xd8\xff\xe0" + b"0" * 10) == "image/jpeg"
    assert vision.sniff_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert vision.sniff_image_type(b"<html>not an image") is None


def test_host_allowlist_matches_exact_and_subdomains_only():
    allowed = ["files.slack.com", "atlassian.net"]
    assert vision.host_allowed("files.slack.com", allowed)
    assert vision.host_allowed("acme.atlassian.net", allowed)
    assert not vision.host_allowed("evilatlassian.net", allowed)
    assert not vision.host_allowed("files.slack.com.evil.io", allowed)


async def test_fetch_refuses_disallowed_hosts_and_private_ips(monkeypatch):
    assert await vision.fetch_image("https://evil.example.com/a.png") is None

    async def private(host):
        return False

    monkeypatch.setattr(vision, "resolves_to_public_ips", private)
    assert await vision.fetch_image("https://files.slack.com/a.png") is None


async def test_literal_and_private_addresses_are_rejected():
    assert await vision.resolves_to_public_ips("127.0.0.1") is False
    assert await vision.resolves_to_public_ips("169.254.169.254") is False  # cloud metadata
    assert vision._is_public_ip("8.8.8.8") and not vision._is_public_ip("10.0.0.5")


async def test_fetch_enforces_byte_cap_type_and_no_redirects(monkeypatch):
    async def public(host):
        return True

    monkeypatch.setattr(vision, "resolves_to_public_ips", public)
    responses = {
        "/ok.png": httpx.Response(200, content=PNG),
        "/huge.png": httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"0" * (6 * 1024 * 1024)),
        "/page.html": httpx.Response(200, content=b"<html></html>"),
        "/redirect.png": httpx.Response(302, headers={"location": "http://169.254.169.254/"}),
    }
    transport = httpx.MockTransport(lambda req: responses[req.url.path])
    monkeypatch.setattr(vision, "_http", httpx.AsyncClient(transport=transport, follow_redirects=False))
    assert (await vision.fetch_image("https://files.slack.com/ok.png")).media_type == "image/png"
    assert await vision.fetch_image("https://files.slack.com/huge.png") is None
    assert await vision.fetch_image("https://files.slack.com/page.html") is None
    assert await vision.fetch_image("https://files.slack.com/redirect.png") is None


def test_scrub_keeps_codes_drops_text():
    e = LLMVisionExtraction(**vision_output(dialog_text="code 123456", contains_sensitive_data=True))
    scrubbed = vision._scrub(e)
    assert scrubbed.error_codes == ["0x80070005"] and "123456" not in scrubbed.dialog_text
    assert scrubbed.os_platform_hint is OSPlatform.WINDOWS


# ---------------------------------------------------------------------------
# Retrieval degradation
# ---------------------------------------------------------------------------


async def test_embedding_outage_still_declines_out_of_scope(env, monkeypatch):
    async def down(text):
        raise httpx.ConnectError("voyage down")

    monkeypatch.setattr(rag, "embed_query", down)
    env["claude"].triage = [triage_output(scope="not_it_support", category="unknown", cited_sop_ids=[])]
    result = await pipeline.process_ticket(ticket(subject="Write my wedding speech"))
    assert result.scope is ScopeVerdict.NOT_IT_SUPPORT and not result.requires_tier2_escalation
    assert "<retrieved_sops>\n(no SOPs matched" in env["claude"].triage_calls()[0]["messages"][0]["content"][0]["text"]


async def test_undocumented_issue_escalates_and_records_knowledge_gap(env):
    env["sb"].sops = []
    env["claude"].triage = [triage_output(action="ESCALATE_TIER_2", cited_sop_ids=[], confidence_score=0.9,
                                           missing_sop_topic="Outlook error 0x800CCC0E on macOS")]
    result = await pipeline.process_ticket(ticket())
    await background.drain()
    assert result.requires_tier2_escalation
    assert env["sb"].telemetry[0]["missing_sop_topic"] == "Outlook error 0x800CCC0E on macOS"
    assert env["sb"].telemetry[0]["estimated_minutes_saved"] == 0


# ---------------------------------------------------------------------------
# Telemetry & ROI endpoint
# ---------------------------------------------------------------------------


async def test_telemetry_kill_switch(env, monkeypatch):
    use_settings(monkeypatch, DISABLED_FEATURES="roi_telemetry")
    await pipeline.process_ticket(ticket())
    await background.drain()
    assert env["sb"].telemetry == []


@pytest.fixture
def api(env):
    from app import main

    with TestClient(main.app) as client:
        yield client


def test_roi_summary_endpoint(api):
    r = api.get("/v1/telemetry/acme-corp/roi-summary?days=7", headers={"x-webhook-secret": "test-webhook-secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["total_hours_saved"] == 5.5 and body["deflection_rate_pct"] == 55.0
    assert body["top_knowledge_gaps"][0]["topic"] == "adobe licence"


def test_roi_summary_auth_tenant_and_gate(api, monkeypatch):
    h = {"x-webhook-secret": "test-webhook-secret"}
    assert api.get("/v1/telemetry/acme-corp/roi-summary", headers={"x-webhook-secret": "x"}).status_code == 401
    assert api.get("/v1/telemetry/globex/roi-summary", headers=h).status_code == 403
    assert api.get("/v1/telemetry/acme-corp/roi-summary?days=0", headers=h).status_code == 422
    use_settings(monkeypatch, DISABLED_FEATURES="roi_telemetry")
    assert api.get("/v1/telemetry/acme-corp/roi-summary", headers=h).status_code == 404


def test_health_reports_tier(api):
    assert api.get("/health").json()["tier"] == ServiceTier.TIER_0_INCENTIVE.value


def test_outage_detection_requires_a_tier2_channel(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN")
    get_settings.cache_clear()
    assert any("SLACK_ESCALATION_CHANNEL" in m for m in get_settings().missing_core_settings())
    monkeypatch.setenv("DISABLED_FEATURES", "outage_detection")
    get_settings.cache_clear()
    assert get_settings().missing_core_settings() == []
    assert not get_settings().feature_enabled(Feature.OUTAGE_DETECTION)


# ---------------------------------------------------------------------------
# Signing, notifier, ticketing attachments
# ---------------------------------------------------------------------------


def test_signature_roundtrip_tamper_and_replay():
    body = signing.canonical_json({"b": 1, "a": 2})
    assert body == b'{"a":2,"b":1}'
    header = signing.sign("s3cret", body, timestamp=1_700_000_000)
    assert signing.verify("s3cret", body, header, 300, now=1_700_000_100)
    assert not signing.verify("s3cret", body + b" ", header, 300, now=1_700_000_100)
    assert not signing.verify("wrong", body, header, 300, now=1_700_000_100)
    assert not signing.verify("s3cret", body, header, 300, now=1_700_001_000)  # replay outside skew
    assert not signing.verify("s3cret", body, "garbage", 300)


async def test_notifier_posts_signed_to_n8n(monkeypatch):
    monkeypatch.setenv("N8N_NOTIFY_WEBHOOK_URL", "https://n8n.example.com/hook")
    monkeypatch.setenv("N8N_OUTBOUND_SIGNING_SECRET", "n8n-secret")
    get_settings.cache_clear()
    seen = {}

    def handler(req):
        seen["sig"] = req.headers[signing.SIGNATURE_HEADER]
        seen["body"] = req.content
        return httpx.Response(200)

    monkeypatch.setattr(notifier, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await notifier.notify_tier2("outage.detected", "acme-corp", {"x": 1}, "text")
    assert signing.verify("n8n-secret", seen["body"], seen["sig"], 300)
    assert json.loads(seen["body"])["event"] == "outage.detected"


async def test_notifier_reports_failure_when_no_channel_accepts(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN")
    get_settings.cache_clear()
    assert await notifier.notify_tier2("outage.detected", "acme-corp", {}, "text") is False


def test_platform_attachments_become_image_urls():
    files = [{"url_private": "https://files.slack.com/a.png", "mimetype": "image/png"},
             {"url_private": "https://files.slack.com/b.pdf", "mimetype": "application/pdf"},
             {"url_private": "http://files.slack.com/c.png", "mimetype": "image/png"}]
    assert ticketing.image_urls(files, url_key="url_private", type_key="mimetype") == ["https://files.slack.com/a.png"]


def test_telemetry_event_rules():
    from app.schemas.ticket import AgentTriageResult, SeverityLevel

    t = ticket()
    resolved = AgentTriageResult(category=TicketCategory.VPN_NETWORK, severity=SeverityLevel.MEDIUM,
                                 confidence_score=0.9, requires_tier2_escalation=False,
                                 cited_sop_ids=["SOP-NET-001"], draft_user_reply="1.")
    e = telemetry.build_event(t, resolved, 0.0012, 850)
    assert e.deflected_bool and e.estimated_minutes_saved == 15 and e.service_tier == "TIER_0_INCENTIVE"
    escalated = resolved.model_copy(update={"requires_tier2_escalation": True})
    assert telemetry.build_event(t, escalated, 0, 1).estimated_minutes_saved == 0


async def test_retry_sweep_delivers_owed_outage_notifications(env, monkeypatch):
    incident = str(uuid.uuid4())

    async def pending(table, filters, columns="*", limit=None):
        assert table == "outage_incidents" and filters["tier2_notified_at"] == "is.null"
        assert filters["tenant_id"] == "eq.acme-corp"
        return [{"incident_id": incident, "title": "VPN down", "ticket_count": 7}]

    monkeypatch.setattr(supabase_client, "select", pending)
    assert await outage.retry_pending_notifications() == 1
    assert env["sb"].marked == [incident] and env["notified"][0]["payload"]["ticket_count"] == 7


async def test_require_n8n_skips_the_slack_fallback(monkeypatch):
    monkeypatch.setenv("N8N_NOTIFY_WEBHOOK_URL", "https://n8n.example.com/hook")
    monkeypatch.setenv("N8N_OUTBOUND_SIGNING_SECRET", "n8n-secret")
    get_settings.cache_clear()
    hosts = []

    def handler(req):
        hosts.append(req.url.host)
        return httpx.Response(503 if req.url.host == "n8n.example.com" else 200, json={"ok": True})

    monkeypatch.setattr(notifier, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await notifier.notify_tier2("sop_draft.created", "acme-corp", {}, "t", require_n8n=True) is False
    assert hosts == ["n8n.example.com"]  # Slack never tried: its fallback has no review buttons
    assert await notifier.notify_tier2("outage.detected", "acme-corp", {}, "t") is True
    assert hosts[-1] == "slack.com"  # ordinary alerts still fall back to Slack
