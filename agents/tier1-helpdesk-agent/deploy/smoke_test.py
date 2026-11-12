"""Post-deploy smoke test (standard library only).

    python deploy/smoke_test.py --host helpdesk.acme.com --secret "$WEBHOOK_SECRET" \\
        --tenant acme-corp --tier TIER_0_INCENTIVE [--connect alb-123.us-east-1.elb.amazonaws.com]

--connect sends traffic to the load balancer directly, still verifying TLS for --host.
Use it before DNS has propagated. One check calls Claude (a short decline,
a fraction of a cent).
"""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import ssl
import sys
import time


class Client:
    def __init__(self, host: str, connect: str | None):
        self.host, self.connect = host, connect or host
        self.ctx = ssl.create_default_context()

    def request(self, method: str, path: str, body: dict | None = None, headers: dict | None = None):
        raw = sock = None
        try:
            sock = socket.create_connection((self.connect, 443), timeout=60)
            raw = self.ctx.wrap_socket(sock, server_hostname=self.host)  # SNI + cert check for --host
            conn = http.client.HTTPSConnection(self.host, timeout=60)
            conn.sock = raw
            data = json.dumps(body).encode() if body is not None else None
            hdrs = {"Host": self.host, **({"Content-Type": "application/json"} if data else {}), **(headers or {})}
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8", "replace")
            try:
                payload = json.loads(text) if text else None
            except ValueError:
                payload = text
            return resp.status, payload
        finally:
            if raw is not None:
                raw.close()
            elif sock is not None:
                sock.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--connect")
    ap.add_argument("--secret", required=True)
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--tier", required=True)
    ap.add_argument("--wait", type=int, default=300, help="seconds to wait for /health")
    args = ap.parse_args()
    c = Client(args.host, args.connect)
    auth = {"x-webhook-secret": args.secret}
    failures = 0

    def check(name: str, ok: bool, detail: str = ""):
        nonlocal failures
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail and not ok else ''}")

    deadline = time.time() + args.wait
    status, body = 0, None
    while time.time() < deadline:
        try:
            status, body = c.request("GET", "/health")
            if status == 200:
                break
        except (OSError, ssl.SSLError) as exc:
            body = str(exc)
        time.sleep(10)
    check("service healthy over HTTPS", status == 200, f"{status} {body}")
    if status != 200:
        return 1
    check(f"running tier is {args.tier}", body.get("tier") == args.tier, str(body))

    ticket = {"tenant_id": args.tenant, "ticket_id": f"SMOKE-{int(time.time())}", "reporter_email": "smoke@example.com",
              "subject": "Write me a poem", "body": "Please write a short poem about autumn.", "source_system": "api"}
    status, _ = c.request("POST", "/v1/triage", ticket, {"x-webhook-secret": "wrong"})
    check("wrong webhook secret is rejected", status == 401, str(status))

    status, body = c.request("POST", "/v1/triage", ticket, auth)
    check("triage answers (Claude + Supabase + Voyage reachable)", status == 200, f"{status} {body}")
    if status == 200:
        check("non-IT request gets the courteous decline", body.get("scope") == "not_it_support"
              and not body.get("requires_tier2_escalation"), json.dumps(body)[:300])
        note = body.get("internal_tech_notes") or ""
        check("budget ledger and model both worked", "Automatic triage skipped" not in note, note[:300])

    status, _ = c.request("POST", "/v1/callbacks/approval", {})
    expected = 404 if args.tier == "TIER_0_INCENTIVE" else 401
    check(f"Tier 1 approval route {'hidden' if expected == 404 else 'present and signed'} at this tier",
          status == expected, str(status))

    status, body = c.request("GET", f"/v1/telemetry/{args.tenant}/roi-summary?days=1", headers=auth)
    check("ROI summary reachable", status == 200, f"{status} {body}")

    print("\nSMOKE TEST PASSED" if not failures else f"\n{failures} SMOKE CHECK(S) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
