"""End-to-end test of the n8n workflows inside a real n8n, against mock Slack and agent.

    N8N_BIN=/path/to/node_modules/n8n/bin/n8n NODE_BIN=/path/to/node24 \\
        python integrations/n8n/test/e2e_n8n.py

The agent side uses the real Python signing code (app/services/signing.py,
app/services/notifier.py), so this proves signatures interoperate across languages:
agent -> n8n webhooks, and n8n -> agent callbacks.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

MOCK_PORT = 18080
N8N_PORT = 5690  # n8n's task broker defaults to 5679, so stay clear of it
SECRETS = {"out": "agent-outbound-secret", "cb": "n8n-callback-secret", "slack": "slack-signing-secret",
           "webhook": "agent-webhook-secret"}
EMAILS = {"U_BOSS": "boss@acme.com", "U_REVIEWER": "it-manager@acme.com"}


class Mock(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def log_message(self, *args):  # quiet
        pass

    def _send(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self, body: bytes):
        parsed = urllib.parse.urlparse(self.path)
        entry = {"method": self.command, "path": parsed.path, "query": dict(urllib.parse.parse_qsl(parsed.query)),
                 "headers": {k.lower(): v for k, v in self.headers.items()}, "raw": body}
        try:
            entry["json"] = json.loads(body) if body else None
        except ValueError:
            entry["json"] = None
        Mock.calls.append(entry)
        return entry

    def do_GET(self):
        e = self._record(b"")
        if e["path"] == "/slack/users.lookupByEmail":
            return self._send(200, {"ok": True, "user": {"id": "U_" + e["query"]["email"].split("@")[0].upper()}})
        if e["path"] == "/slack/users.info":
            return self._send(200, {"ok": True, "user": {"id": e["query"]["user"],
                                                         "profile": {"email": EMAILS.get(e["query"]["user"])}}})
        self._send(404, {"ok": False})

    def do_POST(self):
        from app.services import signing

        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        e = self._record(body)
        if e["path"] == "/slack/chat.postMessage":
            return self._send(200, {"ok": True, "ts": "1700000000.000100"})
        if e["path"].startswith("/slack-response/"):
            return self._send(200, {"ok": True})
        if e["path"] in ("/v1/callbacks/approval", "/v1/callbacks/sop-draft"):
            ok = signing.verify(SECRETS["cb"], body, e["headers"].get("x-agent-signature", ""), 300)
            e["signature_valid"] = ok
            return self._send(200 if ok else 401, {"status": "approved" if ok else "bad signature"})
        if e["path"] == "/v1/reports/weekly":
            ok = e["headers"].get("x-webhook-secret") == SECRETS["webhook"]
            return self._send(200 if ok else 401, {"delivered": ok})
        self._send(404, {"ok": False})


def calls(path: str) -> list[dict]:
    return [c for c in Mock.calls if c["path"] == path]


def wait_for(predicate, timeout=20.0, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for {what}")


def post(url: str, body: bytes, headers: dict[str, str]) -> int:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


def slack_post(url: str, payload: dict, secret: str = SECRETS["slack"]) -> int:
    body = urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return post(url, body, {"Content-Type": "application/x-www-form-urlencoded",
                            "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})


def main() -> int:
    n8n_bin, node_bin = os.environ["N8N_BIN"], os.environ.get("NODE_BIN", "node")
    home = Path(tempfile.mkdtemp(prefix="n8n-e2e-"))
    env = {
        **os.environ,
        "N8N_USER_FOLDER": str(home), "N8N_PORT": str(N8N_PORT), "N8N_LISTEN_ADDRESS": "127.0.0.1",
        "N8N_RUNNERS_BROKER_PORT": str(N8N_PORT + 1),
        "N8N_DIAGNOSTICS_ENABLED": "false", "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
        "N8N_ENCRYPTION_KEY": "e2e-encryption-key", "N8N_SECURE_COOKIE": "false",
        # Required by the workflows (documented in integrations/n8n/README.md):
        "N8N_BLOCK_ENV_ACCESS_IN_NODE": "false", "NODE_FUNCTION_ALLOW_BUILTIN": "crypto",
        "SLACK_API_BASE": f"http://127.0.0.1:{MOCK_PORT}/slack", "SLACK_BOT_TOKEN": "xoxb-test",
        "SLACK_SIGNING_SECRET": SECRETS["slack"], "N8N_AGENT_SIGNING_SECRET": SECRETS["out"],
        "N8N_CALLBACK_SECRET": SECRETS["cb"], "AGENT_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}",
        "AGENT_WEBHOOK_SECRET": SECRETS["webhook"], "AGENT_TENANTS": "acme-corp,globex",
        "SLACK_ALERT_CHANNEL": "C_ALERT", "SLACK_SOP_REVIEW_CHANNEL": "C_SOP", "SLACK_EXEC_CHANNEL": "C_EXEC",
        "PROVISIONING_PROVIDER": "none",
    }
    n8n = [node_bin, n8n_bin]
    workflows = ROOT / "integrations" / "n8n" / "workflows"

    server = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), Mock)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    subprocess.run([*n8n, "import:workflow", "--separate", f"--input={workflows}"], env=env, check=True,
                   capture_output=True)
    listed = subprocess.run([*n8n, "list:workflow"], env=env, check=True, capture_output=True, text=True).stdout
    ids = [line.split("|")[0] for line in listed.splitlines() if "|" in line]
    assert len(ids) == 4, listed
    for wf_id in ids:
        subprocess.run([*n8n, "publish:workflow", f"--id={wf_id}"], env=env, check=True, capture_output=True)

    log = open(home / "n8n.log", "w")
    proc = subprocess.Popen([*n8n, "start"], env=env, stdout=log, stderr=subprocess.STDOUT)
    failures: list[str] = []
    completed = False
    try:
        wait_for(lambda: "Editor is now accessible" in (home / "n8n.log").read_text(), 300, "n8n to start")
        time.sleep(3)
        base = f"http://127.0.0.1:{N8N_PORT}/webhook"
        os.environ.update({"N8N_OUTBOUND_SIGNING_SECRET": SECRETS["out"]})
        from app.config import get_settings
        from app.services import notifier

        get_settings.cache_clear()

        def check(name, fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as exc:  # noqa: BLE001
                failures.append(name)
                print(f"FAIL  {name}: {exc}")

        def agent_post(path, payload) -> bool:
            notifier._http = None  # the cached client is bound to the previous event loop
            return asyncio.run(notifier.post_signed(f"{base}/{path}", payload))

        card = {
            "event": "approval.requested", "approval_id": "11111111-2222-3333-4444-555555555555",
            "callback_nonce": "n" * 48, "tenant_id": "acme-corp", "ticket_id": "SLK-C1-1.1", "thread_id": "th",
            "requester_email": "pat@acme.com", "approver_email": "boss@acme.com", "item_type": "software",
            "item_key": "figma", "item_display_name": "Figma", "justification": "Redesign project",
            "license_cost_usd": 15.0, "expires_at": "2026-11-15T00:00:00Z",
            "callback_url": "http://127.0.0.1/v1/callbacks/approval", "source_system": "slack",
        }

        def approval_card():
            Mock.calls.clear()
            assert agent_post("helpdesk-approvals", card), "agent post_signed returned False"
            wait_for(lambda: calls("/slack/chat.postMessage"), what="Slack DM")
            lookup = calls("/slack/users.lookupByEmail")[0]["query"]["email"]
            assert lookup == "boss@acme.com", lookup
            msg = calls("/slack/chat.postMessage")[0]["json"]
            assert msg["channel"] == "U_BOSS"
            actions = next(b for b in msg["blocks"] if b["type"] == "actions")["elements"]
            value = json.loads(actions[0]["value"])
            assert value == {"k": "approval", "a": card["approval_id"], "n": card["callback_nonce"],
                             "t": "acme-corp", "d": "approved"}, value
            assert calls("/slack/chat.postMessage")[0]["headers"]["authorization"] == "Bearer xoxb-test"

        def bad_agent_signature():
            Mock.calls.clear()
            body = json.dumps(card).encode()
            status = post(f"{base}/helpdesk-approvals", body, {"Content-Type": "application/json",
                                                                "X-Agent-Signature": "t=1,v1=deadbeef"})
            assert status == 401, status
            time.sleep(2)
            assert not calls("/slack/chat.postMessage"), "acted on an unsigned request"

        def provisioning_without_provider_alerts():
            Mock.calls.clear()
            assert agent_post("helpdesk-approvals", {
                "event": "provisioning.requested", "tenant_id": "acme-corp", "ticket_id": "T-9",
                "approval_id": card["approval_id"], "requester_email": "pat@acme.com", "item_type": "software",
                "item_key": "figma", "item_display_name": "Figma", "provisioning_group": "grp-1"})
            wait_for(lambda: calls("/slack/chat.postMessage"), what="manual-provisioning alert")
            msg = calls("/slack/chat.postMessage")[0]["json"]
            assert msg["channel"] == "C_ALERT" and "Manual provisioning needed" in msg["text"], msg

        def denial_notifies_requester():
            Mock.calls.clear()
            assert agent_post("helpdesk-approvals", {
                "event": "approval.denied", "tenant_id": "acme-corp", "approval_id": card["approval_id"],
                "ticket_id": "T-9", "requester_email": "pat@acme.com", "item_display_name": "Figma",
                "decision_note": "Use the shared seat"})
            wait_for(lambda: calls("/slack/chat.postMessage"), what="denial DM")
            msg = calls("/slack/chat.postMessage")[0]["json"]
            assert msg["channel"] == "U_PAT" and "Use the shared seat" in msg["text"], msg

        def approve_click_reaches_agent_signed():
            Mock.calls.clear()
            value = json.dumps({"k": "approval", "a": card["approval_id"], "n": card["callback_nonce"],
                                "t": "acme-corp", "d": "approved"})
            status = slack_post(f"{base}/helpdesk-slack-actions", {
                "type": "block_actions", "user": {"id": "U_BOSS"},
                "response_url": f"http://127.0.0.1:{MOCK_PORT}/slack-response/abc",
                "actions": [{"action_id": "approval_approve", "value": value}]})
            assert status == 200, status
            wait_for(lambda: calls("/slack-response/abc"), what="Slack message update")
            cb = calls("/v1/callbacks/approval")[0]
            assert cb["signature_valid"], "agent rejected n8n's signature"
            assert cb["json"] == {"approval_id": card["approval_id"], "callback_nonce": card["callback_nonce"],
                                  "tenant_id": "acme-corp", "decision": "approved",
                                  "decided_by_email": "boss@acme.com"}, cb["json"]
            update = calls("/slack-response/abc")[0]["json"]
            assert update["replace_original"] is True and "Approved by <@U_BOSS>" in update["text"], update

        def forged_slack_click_rejected():
            Mock.calls.clear()
            status = slack_post(f"{base}/helpdesk-slack-actions", {"type": "block_actions", "user": {"id": "U_BOSS"},
                                                                   "actions": [{"action_id": "approval_approve",
                                                                                "value": "{}"}]}, secret="wrong")
            assert status == 401, status
            time.sleep(2)
            assert not calls("/v1/callbacks/approval"), "forged click reached the agent"

        def notifications_route_to_channels():
            Mock.calls.clear()
            assert agent_post("helpdesk-notify", {"event": "outage.detected", "tenant_id": "acme-corp",
                                                  "data": {"incident_id": "x"}, "text": "Possible outage: VPN"})
            assert agent_post("helpdesk-notify", {"event": "sop_draft.created", "tenant_id": "acme-corp",
                                                  "data": {"draft_id": "d-1", "doc_id": "SOP-X-1", "title": "Fix X",
                                                           "markdown": "---\\ndoc_id: SOP-X-1\\n---\\n# Fix X",
                                                           "source_ticket_id": "T-77", "redactions": ["email"]},
                                                  "text": "New SOP draft"})
            assert agent_post("helpdesk-notify", {"event": "report.weekly", "tenant_id": "acme-corp",
                                                  "data": {"markdown": "| Metric | x |"}, "text": "report"})
            wait_for(lambda: len(calls("/slack/chat.postMessage")) >= 3, what="three channel posts")
            by_channel = {c["json"]["channel"]: c["json"] for c in calls("/slack/chat.postMessage")}
            assert by_channel["C_ALERT"]["text"] == "Possible outage: VPN"
            sop_value = json.loads(next(b for b in by_channel["C_SOP"]["blocks"]
                                        if b["type"] == "actions")["elements"][0]["value"])
            assert sop_value == {"k": "sop", "t": "acme-corp", "d": "d-1", "x": "approve"}, sop_value
            assert "| Metric | x |" in json.dumps(by_channel["C_EXEC"]["blocks"])

        def sop_click_reaches_agent_signed():
            Mock.calls.clear()
            value = json.dumps({"k": "sop", "t": "acme-corp", "d": "d-1", "x": "approve"})
            status = slack_post(f"{base}/helpdesk-slack-actions", {
                "type": "block_actions", "user": {"id": "U_REVIEWER"},
                "response_url": f"http://127.0.0.1:{MOCK_PORT}/slack-response/sop",
                "actions": [{"action_id": "sop_ingest", "value": value}]})
            assert status == 200
            wait_for(lambda: calls("/slack-response/sop"), what="SOP message update")
            cb = calls("/v1/callbacks/sop-draft")[0]
            assert cb["signature_valid"] and cb["json"] == {"tenant_id": "acme-corp", "draft_id": "d-1",
                                                            "decision": "approve",
                                                            "reviewer_email": "it-manager@acme.com"}, cb["json"]

        def weekly_reports_for_each_tenant():
            Mock.calls.clear()
            wf_id = next(line.split("|")[0] for line in listed.splitlines() if "weekly" in line.lower())
            # `n8n execute` boots its own task broker, so give it a free port.
            run = subprocess.run([*n8n, "execute", f"--id={wf_id}"], capture_output=True, text=True, timeout=300,
                                 env={**env, "N8N_RUNNERS_BROKER_PORT": str(N8N_PORT + 2)})
            assert run.returncode == 0, (run.stdout + run.stderr)[-1500:]
            tenants = sorted(c["query"]["tenant_id"] for c in calls("/v1/reports/weekly"))
            assert tenants == ["acme-corp", "globex"], tenants
            assert all(c["headers"]["x-webhook-secret"] == SECRETS["webhook"] for c in calls("/v1/reports/weekly"))

        def deploy_ping_checks_secret_and_reachability():
            sys.path.insert(0, str(ROOT / "deploy"))
            import n8n_ping

            Mock.calls.clear()
            for path in ("helpdesk-approvals", "helpdesk-notify"):
                ok, detail = n8n_ping.ping(f"{base}/{path}", SECRETS["out"], allow_http=True)
                assert ok, f"{path}: {detail}"
                ok, detail = n8n_ping.ping(f"{base}/{path}", "wrong-secret", allow_http=True)
                assert not ok and "N8N_AGENT_SIGNING_SECRET" in detail, detail
            ok, detail = n8n_ping.ping(f"{base}/not-a-workflow", SECRETS["out"], allow_http=True)
            assert not ok and "publish" in detail, detail
            time.sleep(2)
            assert not calls("/slack/chat.postMessage"), "a ping posted to Slack"

        def schedule_has_missed_run_policy():
            exported = subprocess.run([*n8n, "export:workflow", "--all"], env={**env, "N8N_RUNNERS_BROKER_PORT":
                                      str(N8N_PORT + 3)}, capture_output=True, text=True, timeout=300).stdout
            assert '"misfirePolicy": "coalesce"' in exported or '"misfirePolicy":"coalesce"' in exported, \
                "schedule trigger lost its missed-run policy on import"

        for name, fn in [
            ("deploy ping: reachable + secret match, mismatch and unpublished explained", deploy_ping_checks_secret_and_reachability),
            ("weekly schedule keeps its missed-run (coalesce) policy", schedule_has_missed_run_policy),
            ("agent approval.requested -> approver DM with signed-nonce buttons", approval_card),
            ("unsigned agent webhook -> 401, no side effects", bad_agent_signature),
            ("provisioning with no provider -> manual-provisioning alert", provisioning_without_provider_alerts),
            ("approval.denied -> requester DM with note", denial_notifies_requester),
            ("Slack [Approve] -> signed callback the agent accepts + message update", approve_click_reaches_agent_signed),
            ("forged Slack click -> 401, agent never called", forged_slack_click_rejected),
            ("notifications -> alert / SOP review / exec channels", notifications_route_to_channels),
            ("Slack [Ingest] -> signed SOP-draft callback", sop_click_reaches_agent_signed),
            ("weekly schedule -> report request per tenant", weekly_reports_for_each_tenant),
        ]:
            check(name, fn)
        completed = True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        server.shutdown()
        if failures or not completed:
            print(f"\nn8n log kept at: {home / 'n8n.log'}")
        else:
            shutil.rmtree(home, ignore_errors=True)
    print(f"\n{'ALL N8N E2E CHECKS PASSED' if not failures else f'{len(failures)} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
