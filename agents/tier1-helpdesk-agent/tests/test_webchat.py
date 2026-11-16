"""Public website chat: 5 free questions, sign-up, a 10-question trial, then credit packs or a monthly plan.

The metering SQL itself is tested against real Postgres in supabase/tests/. Here
the fake mirrors it, and the tests cover everything around it: who may call,
what is counted, what a visitor is told, and that the chat has no side effects.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.schemas.ticket import WebChatGrant, WebChatRequest
from app.services import background, guardrails, signing, webchat
from tests.fakes import triage_output

SECRET = "webchat-signing-secret-0123456789abcdef"
VISITOR = "visitor-0123456789abcdef"


@pytest.fixture
def chat(env, monkeypatch):
    monkeypatch.setenv("WEBCHAT_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("WEBCHAT_SIGNUP_URL", "https://valleyagentworks.com/helpdesk/signup")
    monkeypatch.setenv("WEBCHAT_UPGRADE_URL", "https://valleyagentworks.com/helpdesk/plans")
    get_settings.cache_clear()
    return env


def ask(message="My VPN won't connect", **kw) -> WebChatRequest:
    return WebChatRequest(**{"visitor_id": VISITOR, "client_ip": "203.0.113.7", "message": message, **kw})


def signed(client: TestClient, path: str, payload: dict, secret: str = SECRET):
    body = signing.canonical_json(payload)
    return client.post(path, content=body, headers={"Content-Type": "application/json",
                                                    signing.SIGNATURE_HEADER: signing.sign(secret, body)})


# --- metering ---------------------------------------------------------------------


async def test_five_free_questions_then_sign_up_is_required(chat):
    for n in range(1, 6):
        reply = await webchat.handle_message(ask())
        assert reply.status == "answered" and reply.used == n and reply.remaining == 5 - n
    calls_before = len(chat["claude"].requests)
    reply = await webchat.handle_message(ask())
    assert reply.status == "signup_required" and reply.signup_url.endswith("/helpdesk/signup")
    assert "5 free questions" in reply.reply and "10 more free questions" in reply.reply
    assert len(chat["claude"].requests) == calls_before  # the sixth question never reaches the model


async def test_new_cookie_from_the_same_network_does_not_restart_the_trial(chat, monkeypatch):
    monkeypatch.setenv("WEBCHAT_IP_DAILY_FREE_USES", "6")
    get_settings.cache_clear()
    for i in range(6):
        fresh = f"visitor-{i:016d}"
        assert (await webchat.handle_message(ask(visitor_id=fresh))).status == "answered"
    assert (await webchat.handle_message(ask(visitor_id="visitor-brand-new-00001"))).status == "signup_required"


async def test_default_pricing_flow_trial_then_credit_pack_then_monthly_plans(chat):
    """5 anonymous questions, sign up, 10 trial questions, then a 50-question pack or a plan."""
    me = {"account_email": "pat@example.com"}
    for n in range(1, 11):
        reply = await webchat.handle_message(ask(**me))
        assert reply.status == "answered" and reply.plan == "free" and reply.remaining == 10 - n
    asked = len(chat["claude"].requests)
    blocked = await webchat.handle_message(ask(**me))
    assert blocked.status == "limit_reached" and "free trial questions" in blocked.reply
    assert "credit pack" in blocked.reply and "monthly plan" in blocked.reply
    assert len(chat["claude"].requests) == asked  # question 11 never reaches the model

    # A $20 pack is 50 questions (the price lives on the website; the agent counts questions).
    await webchat.apply_grant(WebChatGrant(grant_id="pay_pack_0001", email="pat@example.com", add_credits=50))
    after_pack = await webchat.handle_message(ask(**me))
    assert after_pack.status == "answered" and after_pack.remaining == 49

    # The two paid tiers: a monthly allowance, with unused credits kept on top.
    await webchat.apply_grant(WebChatGrant(grant_id="pay_sub_0001", email="pat@example.com", plan="starter"))
    starter = await webchat.handle_message(ask(**me))
    assert (starter.plan, starter.allowance, starter.used, starter.credits) == ("starter", 250, 1, 49)
    assert starter.remaining == 249 + 49
    await webchat.apply_grant(WebChatGrant(grant_id="pay_sub_0002", email="pat@example.com", plan="pro"))
    assert (await webchat.handle_message(ask(**me))).allowance == 600


async def test_the_trial_is_granted_once_even_if_the_first_thing_they_do_is_pay(chat):
    grant = WebChatGrant(grant_id="pay_first_0001", email="buyer@example.com", add_credits=50)
    assert (await webchat.apply_grant(grant))["credits"] == 60  # 10 trial + 50 bought
    await webchat.apply_grant(WebChatGrant(grant_id="pay_first_0002", email="buyer@example.com", add_credits=50))
    assert chat["sb"].webchat_accounts["buyer@example.com"]["credits"] == 110  # no second trial


async def test_monthly_allowance_then_credits_then_limit(chat, monkeypatch):
    monkeypatch.setenv("WEBCHAT_PLAN_MONTHLY_LIMITS", json.dumps({"free": 2, "starter": 4}))
    monkeypatch.setenv("WEBCHAT_SIGNUP_FREE_QUESTIONS", "0")
    get_settings.cache_clear()
    me = {"account_email": "Pat@Example.com"}
    first = await webchat.handle_message(ask(**me))
    assert first.status == "answered" and first.signed_in and first.plan == "free" and first.allowance == 2
    await webchat.handle_message(ask(**me))
    blocked = await webchat.handle_message(ask(**me))
    assert blocked.status == "limit_reached" and blocked.upgrade_url.endswith("/plans")
    assert "credit pack" in blocked.reply and "move up a plan" in blocked.reply

    # A credit pack: applied once per payment, spent after the allowance.
    grant = WebChatGrant(grant_id="pay_credit_0001", email="pat@example.com", add_credits=2)
    assert (await webchat.apply_grant(grant)) == {"applied": True, "plan": "free", "credits": 2}
    assert (await webchat.apply_grant(grant))["applied"] is False  # webhook retry: no double credit
    spent = await webchat.handle_message(ask(**me))
    assert spent.status == "answered" and spent.credits == 1

    # Upgrading gives the larger allowance straight away and keeps the credits.
    await webchat.apply_grant(WebChatGrant(grant_id="pay_plan_0001", email="pat@example.com", plan="starter"))
    upgraded = await webchat.handle_message(ask(**me))
    assert upgraded.plan == "starter" and upgraded.allowance == 4 and upgraded.credits == 1


async def test_unknown_plan_is_refused(chat):
    with pytest.raises(webchat.InvalidRequest):
        await webchat.apply_grant(WebChatGrant(grant_id="pay_plan_0002", email="pat@example.com", plan="platinum"))


async def test_a_question_that_gets_no_answer_is_not_counted(chat):
    chat["sb"].cap_ok = False  # the website chat's own monthly model budget is used up
    reply = await webchat.handle_message(ask())
    assert reply.status == "unavailable" and reply.used == 0 and "wasn't counted" in reply.reply
    assert chat["sb"].webchat_visitors[VISITOR] == 0
    chat["sb"].cap_ok = True
    assert (await webchat.handle_message(ask())).used == 1


async def test_metering_outage_fails_closed(chat):
    chat["sb"].webchat_down = True
    reply = await webchat.handle_message(ask())
    assert reply.status == "unavailable" and chat["claude"].requests == []  # no count, no answer


# --- what a visitor is told ---------------------------------------------------------


async def test_grounded_answer_cites_its_sop(chat):
    reply = await webchat.handle_message(ask())
    assert reply.outcome == "resolved" and reply.cited_sop_ids == ["SOP-NET-001"]
    assert "GlobalProtect" in reply.reply


async def test_out_of_scope_gets_the_courteous_decline(chat):
    chat["claude"].triage = [triage_output(scope="not_it_support", category="unknown", cited_sop_ids=[])]
    reply = await webchat.handle_message(ask("Write me a poem about autumn"))
    assert reply.outcome == "declined" and "outside what I can help with" in reply.reply


@pytest.mark.parametrize("output, phrase", [
    (dict(confidence_score=0.3), "I won't guess"),
    (dict(scope="beyond_tier1", cited_sop_ids=[]), "administrator access"),
    (dict(category="security_incident"), "possible security issue"),
    (dict(category="software_access", requested_item="Figma", action="AWAIT_MANAGER_APPROVAL"), "I won't guess"),
])
async def test_escalations_never_promise_a_technician_who_is_not_there(chat, output, phrase):
    chat["claude"].triage = [triage_output(**output)] * 2
    reply = await webchat.handle_message(ask())
    await background.drain()
    assert reply.outcome == "escalated" and phrase in reply.reply and "nobody will follow up" in reply.reply
    assert "reference number" not in reply.reply and "will follow up with you" not in reply.reply
    # No side effects for an anonymous visitor: nobody is paged, nothing is provisioned.
    assert chat["notified"] == [] and chat["n8n"].posts == []


async def test_sandbox_runs_no_outage_detection_threads_or_lookups(chat, tier1):
    chat["sb"].burst_rows = [{"ticket_id": f"B{i}", "summary": "VPN", "category": "vpn_network",
                              "parent_incident_id": None, "similarity": 0.99, "created_at": None,
                              "reporter_email": f"u{i}@x.com"} for i in range(9)]
    reply = await webchat.handle_message(ask())
    await background.drain()
    assert reply.status == "answered"
    called = {fn for fn, _ in chat["sb"].rpc_calls}
    assert not called & {"match_recent_tickets", "register_outage_cluster", "increment_clarification"}
    assert chat["sb"].t("recent_tickets") == [] and chat["sb"].t("conversation_threads") == []


async def test_chat_spends_against_its_own_tenant_and_budget(chat):
    await webchat.handle_message(ask())
    reservation = chat["sb"].reservations[0]
    assert reservation["p_tenant_id"] == "web-demo" and reservation["p_cap_usd"] == 10.0
    assert reservation["p_ticket_id"].startswith("WEB-")


async def test_internal_notes_never_reach_the_visitor(chat):
    chat["claude"].triage = [triage_output(confidence_score=0.2, internal_tech_notes="INTERNAL-ONLY-NOTE",
                                           draft_user_reply="UNVERIFIED-DRAFT")]
    reply = await webchat.handle_message(ask())
    dumped = reply.model_dump_json()
    assert "INTERNAL-ONLY-NOTE" not in dumped and "UNVERIFIED-DRAFT" not in dumped


# --- input handling -----------------------------------------------------------------


def test_addresses_are_stored_only_as_keyed_hashes(chat):
    a, b = webchat.hash_ip("203.0.113.7"), webchat.hash_ip("203.0.113.8")
    assert len(a) == 64 and a != b and "203" not in a
    # One IPv6 customer network (/64) is one visitor network, not 2^64 of them.
    assert webchat.hash_ip("2001:db8:1:2::1") == webchat.hash_ip("2001:db8:1:2:ffff::9")
    assert webchat.hash_ip("::ffff:203.0.113.7") == a
    with pytest.raises(webchat.InvalidRequest):
        webchat.hash_ip("not-an-ip")


async def test_overlong_or_empty_messages_are_rejected_without_being_counted(chat):
    for bad in ("x" * 1001, "\x00\x01 "):
        with pytest.raises(webchat.InvalidRequest):
            await webchat.handle_message(ask(bad))
    assert chat["sb"].webchat_visitors == {} and chat["claude"].requests == []


def test_request_schema_rejects_forged_shapes():
    for bad in ({"visitor_id": "short"}, {"visitor_id": "x" * 16 + "/../"}, {"account_email": "not-an-email"},
                {"extra_field": 1}):
        with pytest.raises(ValueError):
            WebChatRequest(**{"visitor_id": VISITOR, "client_ip": "203.0.113.7", "message": "hello", **bad})
    with pytest.raises(ValueError):
        WebChatGrant(grant_id="pay_nothing_1", email="pat@example.com")  # must do something


# --- the HTTP surface -----------------------------------------------------------------


def test_routes_are_hidden_unless_enabled(env):
    from app import main

    with TestClient(main.app) as client:
        for path in ("/v1/webchat/message", "/v1/webchat/grants", "/v1/webchat/account"):
            assert signed(client, path, {}).status_code == 404, path


def test_only_the_websites_server_can_call(chat):
    from app import main

    payload = {"visitor_id": VISITOR, "client_ip": "203.0.113.7", "message": "My VPN won't connect"}
    with TestClient(main.app) as client:
        assert client.post("/v1/webchat/message", json=payload).status_code == 401  # a browser, unsigned
        assert signed(client, "/v1/webchat/message", payload, secret="wrong-secret").status_code == 401
        # The API's own webhook secret is a different credential and opens nothing here.
        assert client.post("/v1/webchat/message", json=payload,
                           headers={"x-webhook-secret": "test-webhook-secret"}).status_code == 401
        stale = signing.canonical_json(payload)
        assert client.post("/v1/webchat/message", content=stale, headers={
            signing.SIGNATURE_HEADER: signing.sign(SECRET, stale, timestamp=1_700_000_000)}).status_code == 401
        assert chat["claude"].requests == [] and chat["sb"].webchat_visitors == {}

        ok = signed(client, "/v1/webchat/message", payload)
        assert ok.status_code == 200 and ok.json()["status"] == "answered" and ok.json()["remaining"] == 4
        assert set(ok.json()) == {"status", "outcome", "reply", "cited_sop_ids", "signed_in", "plan", "used",
                                  "allowance", "remaining", "credits", "signup_url", "upgrade_url"}
        assert signed(client, "/v1/webchat/message", {**payload, "message": "x" * 1001}).status_code == 422
        assert signed(client, "/v1/webchat/message", {**payload, "client_ip": "nope"}).status_code == 422


def test_grants_and_account_lookup_over_http(chat):
    from app import main

    with TestClient(main.app) as client:
        r = signed(client, "/v1/webchat/grants", {"grant_id": "pay_http_0001", "email": "Pat@Example.com",
                                                  "plan": "starter", "add_credits": 100})
        assert r.status_code == 200 and r.json() == {"applied": True, "plan": "starter", "credits": 110}  # + trial
        assert signed(client, "/v1/webchat/grants", {"grant_id": "pay_http_0002", "email": "pat@example.com",
                                                     "plan": "platinum"}).status_code == 422
        assert client.post("/v1/webchat/grants", json={"grant_id": "pay_evil_0001", "email": "eve@example.com",
                                                       "add_credits": 99999}).status_code == 401
        chat["sb"].t("webchat_accounts").append({"email": "pat@example.com", "plan": "starter", "credits": 100,
                                                 "period_start": "2020-01-01", "period_uses": 77})
        acct = signed(client, "/v1/webchat/account", {"email": "pat@example.com"}).json()
        assert acct == {"email": "pat@example.com", "plan": "starter", "used": 0, "allowance": 250, "credits": 100}
        assert signed(client, "/v1/webchat/account", {"email": "nobody@example.com"}).status_code == 404


# --- configuration --------------------------------------------------------------------


def test_startup_requires_a_real_secret_and_a_separate_tenant():
    base = dict(tenant_id="acme-corp", webhook_secret="x", supabase_url="https://x.supabase.co",
                supabase_service_key="k", voyage_api_key="v", anthropic_api_key="a",
                slack_bot_token="xoxb", slack_escalation_channel="C1", webchat_enabled=True)
    missing = Settings(_env_file=None, **base, webchat_signing_secret="short").missing_core_settings()
    assert any("WEBCHAT_SIGNING_SECRET" in m for m in missing)
    shared = Settings(_env_file=None, **base, webchat_signing_secret=SECRET, webchat_tenant_id="acme-corp")
    assert any("WEBCHAT_TENANT_ID" in m for m in shared.missing_core_settings())
    assert Settings(_env_file=None, **base, webchat_signing_secret=SECRET).missing_core_settings() == []
    assert guardrails.TRIAGE_UNAVAILABLE == "triage_unavailable"


# --- the website's real signing code against the real app ------------------------------


def test_request_signed_by_the_website_code_is_accepted_by_the_agent(chat):
    import shutil
    import subprocess
    from pathlib import Path

    from app import main

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = Path(__file__).resolve().parents[1] / "integrations" / "website" / "test" / "sign_request.mjs"
    payload = {"visitor_id": VISITOR, "client_ip": "203.0.113.7", "account_email": None,
               "message": "My VPN won't connect — «portal not found»"}
    out = subprocess.run([node, str(script), SECRET, json.dumps(payload)], capture_output=True, text=True,
                         check=True, timeout=60).stdout
    request = json.loads(out)
    with TestClient(main.app) as client:
        r = client.post("/v1/webchat/message", content=request["body"].encode(),
                        headers={"Content-Type": "application/json", signing.SIGNATURE_HEADER: request["signature"]})
        assert r.status_code == 200 and r.json()["status"] == "answered", r.text
        tampered = request["body"].replace("203.0.113.7", "203.0.113.8").encode()
        assert client.post("/v1/webchat/message", content=tampered,
                           headers={signing.SIGNATURE_HEADER: request["signature"]}).status_code == 401
