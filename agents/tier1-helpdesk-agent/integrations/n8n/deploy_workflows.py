"""Create or update the help desk workflows in an n8n instance and publish them.

    N8N_API_URL=https://n8n.example.com N8N_API_KEY=... python integrations/n8n/deploy_workflows.py

Uses the n8n public API (Settings > n8n API to create a key). Workflows are matched
by name, so re-running updates them in place instead of duplicating them. Publishing
is POST /workflows/{id}/publish on n8n 2.x, falling back to /activate on 1.x.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
# The public API rejects any other top-level or settings fields (additionalProperties: false).
ALLOWED_TOP = {"name", "nodes", "connections", "settings"}
ALLOWED_SETTINGS = {"saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution",
                    "saveDataSuccessExecution", "executionTimeout", "errorWorkflow", "timezone", "executionOrder"}


def api_body(workflow: dict) -> dict:
    body = {k: v for k, v in workflow.items() if k in ALLOWED_TOP}
    body["settings"] = {k: v for k, v in workflow.get("settings", {}).items() if k in ALLOWED_SETTINGS}
    description = (workflow.get("meta") or {}).get("description")
    if description:
        body["description"] = description
    return body


def main() -> int:
    base = os.environ["N8N_API_URL"].rstrip("/") + "/api/v1"
    headers = {"X-N8N-API-KEY": os.environ["N8N_API_KEY"], "Accept": "application/json"}
    with httpx.Client(base_url=base, headers=headers, timeout=30.0) as client:
        for path in sorted((HERE / "workflows").glob("*.json")):
            workflow = json.loads(path.read_text())
            body = api_body(workflow)
            found = client.get("/workflows", params={"name": workflow["name"]})
            found.raise_for_status()
            existing = [w for w in found.json().get("data", []) if w.get("name") == workflow["name"]]
            if existing:
                wf_id = existing[0]["id"]
                resp = client.put(f"/workflows/{wf_id}", json=body)
                action = "updated"
            else:
                resp = client.post("/workflows", json=body)
                action = "created"
            if resp.status_code >= 300:
                print(f"{path.name}: {action} failed ({resp.status_code}): {resp.text[:400]}")
                return 1
            wf_id = resp.json()["id"]
            published = client.post(f"/workflows/{wf_id}/publish")
            if published.status_code == 404:
                published = client.post(f"/workflows/{wf_id}/activate")
            if published.status_code >= 300:
                print(f"{path.name}: publish failed ({published.status_code}): {published.text[:400]}")
                return 1
            print(f"{path.name}: {action} and published (id {wf_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
