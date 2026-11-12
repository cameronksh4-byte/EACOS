import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.schemas.ticket import AgentTriageResult, ScopeVerdict, SeverityLevel, TicketCategory
from app.services import pipeline, ticketing

PAYLOAD = {
    "tenant_id": "acme-corp",
    "ticket_id": "T-1",
    "reporter_email": "pat@acme.com",
    "subject": "Write my wedding speech",
    "body": "Please write a 5 minute speech.",
    "source_system": "zendesk",
}


@pytest.fixture
def client(monkeypatch):
    async def fake_process(ticket):
        return AgentTriageResult(
            scope=ScopeVerdict.NOT_IT_SUPPORT,
            category=TicketCategory.UNKNOWN,
            severity=SeverityLevel.LOW,
            confidence_score=0.99,
            requires_tier2_escalation=False,
            cited_sop_ids=[],
            draft_user_reply="declined",
        )

    monkeypatch.setattr(main, "process_ticket", fake_process)
    monkeypatch.setattr(pipeline, "process_ticket", fake_process)
    with TestClient(main.app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json()["status"] == "healthy"


def test_triage_rejects_bad_secret(client):
    r = client.post("/v1/triage", json=PAYLOAD, headers={"x-webhook-secret": "nope"})
    assert r.status_code == 401


def test_triage_rejects_missing_secret(client):
    assert client.post("/v1/triage", json=PAYLOAD).status_code == 422


def test_triage_rejects_other_tenant(client):
    r = client.post(
        "/v1/triage", json={**PAYLOAD, "tenant_id": "globex"}, headers={"x-webhook-secret": "test-webhook-secret"}
    )
    assert r.status_code == 403


def test_triage_happy_path(client):
    r = client.post("/v1/triage", json=PAYLOAD, headers={"x-webhook-secret": "test-webhook-secret"})
    assert r.status_code == 200
    assert r.json()["scope"] == "not_it_support"


def _slack_headers(body: bytes, secret="slack-secret", ts=None):
    ts = str(int(ts or time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"x-slack-request-timestamp": ts, "x-slack-signature": sig, "content-type": "application/json"}


def test_slack_url_verification(client):
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    r = client.post("/webhooks/slack", content=body, headers=_slack_headers(body))
    assert r.json() == {"challenge": "abc"}


def test_slack_rejects_bad_and_stale_signatures(client):
    body = b'{"type":"url_verification","challenge":"abc"}'
    assert client.post("/webhooks/slack", content=body, headers=_slack_headers(body, "wrong")).status_code == 401
    old = _slack_headers(body, ts=time.time() - 3600)
    assert client.post("/webhooks/slack", content=body, headers=old).status_code == 401


def test_jira_rejects_bad_signature(client):
    r = client.post("/webhooks/jira", content=b"{}", headers={"x-hub-signature": "sha256=deadbeef"})
    assert r.status_code == 401


def test_jira_ignores_non_create_events(client):
    body = json.dumps({"webhookEvent": "jira:issue_updated"}).encode()
    sig = "sha256=" + hmac.new(b"jira-secret", body, hashlib.sha256).hexdigest()
    r = client.post("/webhooks/jira", content=body, headers={"x-hub-signature": sig})
    assert r.json()["ignored"] is True


def test_teams_signature_verification():
    secret = base64.b64encode(b"teams-shared-secret").decode()
    body = b'{"text":"hi"}'
    good = "HMAC " + base64.b64encode(hmac.new(b"teams-shared-secret", body, hashlib.sha256).digest()).decode()
    assert ticketing.verify_teams_signature(secret, body, good)
    assert not ticketing.verify_teams_signature(secret, body, "HMAC AAAA")
    assert not ticketing.verify_teams_signature("", body, good)


def test_slack_ignores_bots_and_strips_mentions():
    assert ticketing.slack_event_to_ticket({"type": "app_mention", "bot_id": "B1", "text": "x"}) is None
    parsed = ticketing.slack_event_to_ticket(
        {"type": "app_mention", "channel": "C1", "ts": "1.2", "text": "<@U123> my VPN is down"}
    )
    assert parsed == ("C1", "1.2", "my VPN is down")


def test_jira_adf_description_is_flattened():
    issue = {
        "key": "HD-7",
        "fields": {
            "summary": "Printer offline",
            "reporter": {"emailAddress": "sam@acme.com"},
            "description": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "3rd floor printer"}]}
            ]},
        },
    }
    ticket = ticketing.jira_issue_to_ticket(issue)
    assert ticket.ticket_id == "HD-7" and "3rd floor printer" in ticket.body
