"""Check the agent can reach your n8n workflows, and that the shared secret matches.

    python deploy/n8n_ping.py deploy/acme-corp.env

Sends a signed no-op event ("deploy.ping") to each n8n webhook the agent uses,
through the same public URLs the agent in AWS calls. The workflows verify the
signature and ignore the event, so nothing is posted anywhere.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "deploy"))

from app.services import signing  # noqa: E402
import render_config  # noqa: E402

HINTS = {
    401: "n8n rejected the signature: N8N_AGENT_SIGNING_SECRET in n8n must equal N8N_OUTBOUND_SIGNING_SECRET "
         "in deploy.env. Reload the generated n8n env file and restart n8n",
    404: "no published workflow at this path: import integrations/n8n/workflows/*.json and publish them",
    403: "the tunnel or a firewall blocked the request: allow POST to /webhook/* on the public n8n URL",
}


def ping(url: str, secret: str, allow_http: bool = False) -> tuple[bool, str]:
    if not allow_http and not url.startswith("https://"):
        return False, "must be https:// (the agent in AWS only calls public HTTPS)"
    body = signing.canonical_json({"event": "deploy.ping", "tenant_id": "deploy-check", "data": {}})
    try:
        resp = httpx.post(url, content=body, timeout=20.0, headers={
            "Content-Type": "application/json", signing.SIGNATURE_HEADER: signing.sign(secret, body)})
    except httpx.HTTPError as exc:
        return False, (f"not reachable ({type(exc).__name__}). Is n8n running and the tunnel up? "
                       "The agent in AWS needs this URL to be public")
    if resp.status_code == 200:
        return True, "reachable, signature accepted"
    return False, f"HTTP {resp.status_code}: {HINTS.get(resp.status_code, resp.text[:200])}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("env_file", type=Path)
    ap.add_argument("--allow-http", action="store_true", help="tests only")
    args = ap.parse_args(argv)
    cfg = render_config.effective(render_config.parse_env_file(args.env_file))
    secret = cfg.get("N8N_OUTBOUND_SIGNING_SECRET", "")
    targets = [(k, cfg[k]) for k in ("N8N_APPROVAL_WEBHOOK_URL", "N8N_NOTIFY_WEBHOOK_URL") if cfg.get(k)]
    if not targets:
        print("no n8n webhooks configured; nothing to check")
        return 0
    if not secret:
        print("N8N_OUTBOUND_SIGNING_SECRET is empty in deploy.env")
        return 1
    failed = 0
    for name, url in targets:
        ok, detail = ping(url, secret, args.allow_http)
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name} {url}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
