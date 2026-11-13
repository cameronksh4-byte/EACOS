"""Assemble importable n8n workflow JSON from the Code-node sources in src/.

    python integrations/n8n/build_workflows.py          # writes workflows/*.json
    python integrations/n8n/build_workflows.py --check  # CI: fail if workflows/ is stale

Each Code node gets src/lib.js followed by its handler, so the logic lives in
plain .js files (unit-tested with `node --test`) and the JSON is generated.
IDs are derived from names, so rebuilding produces identical files.
"""

from __future__ import annotations

import itertools
import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
OUT = HERE / "workflows"
NS = uuid.UUID("6f1c3b0e-6b1f-4a8e-9a43-1d2f7e0c5a10")

WEBHOOK = ("n8n-nodes-base.webhook", 2.2)
RESPOND = ("n8n-nodes-base.respondToWebhook", 1.5)
CODE = ("n8n-nodes-base.code", 2)
SCHEDULE = ("n8n-nodes-base.scheduleTrigger", 1.4)
MANUAL = ("n8n-nodes-base.manualTrigger", 1)


def _id(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


def code_source(handler: str) -> str:
    lib = (SRC / "lib.js").read_text()
    body = (SRC / handler).read_text()
    return f"{lib}\n// ---- {handler} ----\n{body}"


def node(workflow: str, name: str, kind: tuple[str, float], position: list[int], parameters: dict, **extra) -> dict:
    out = {
        "id": _id(workflow, name),
        "name": name,
        "type": kind[0],
        "typeVersion": kind[1],
        "position": position,
        "parameters": parameters,
    }
    out.update(extra)
    return out


def chain(*names: str) -> dict:
    return {a: {"main": [[{"node": b, "type": "main", "index": 0}]]} for a, b in itertools.pairwise(names)}


def webhook_workflow(name: str, path: str, verify_src: str, handler_src: str, description: str) -> dict:
    nodes = [
        node(name, "Webhook", WEBHOOK, [0, 0], {
            "httpMethod": "POST",
            "path": path,
            "responseMode": "responseNode",
            "options": {"rawBody": True},
        }, webhookId=_id(name, "webhook-id")),
        node(name, "Verify signature", CODE, [240, 0], {"mode": "runOnceForAllItems", "jsCode": code_source(verify_src)}),
        node(name, "Respond", RESPOND, [480, 0], {
            "respondWith": "json",
            "responseBody": "={{ JSON.stringify({ ok: $json.ok }) }}",
            "options": {"responseCode": "={{ $json.status }}"},
        }),
        node(name, "Handle", CODE, [720, 0], {"mode": "runOnceForAllItems", "jsCode": code_source(handler_src)}),
    ]
    return {
        "name": name,
        "nodes": nodes,
        "connections": chain("Webhook", "Verify signature", "Respond", "Handle"),
        "settings": {"executionOrder": "v1", "saveDataSuccessExecution": "none", "saveDataErrorExecution": "all"},
        "meta": {"templateCredsSetupCompleted": True, "description": description},
        "pinData": {},
        "active": False,
        "tags": [],
    }


def schedule_workflow() -> dict:
    name = "Help desk: weekly executive reports"
    nodes = [
        node(name, "Every Monday 08:00", SCHEDULE, [0, 0], {
            "rule": {"interval": [{"field": "cronExpression", "expression": "0 8 * * 1"}]},
            # A local n8n may be off at 08:00 Monday: run the missed report once it's back.
            "misfirePolicy": "coalesce",
        }),
        # Lets operators (and `n8n execute`) send the reports on demand.
        node(name, "Run now", MANUAL, [0, 200], {}),
        node(name, "Request reports", CODE, [240, 100], {"mode": "runOnceForAllItems",
                                                        "jsCode": code_source("weekly_reports.js")}),
    ]
    return {
        "name": name,
        "nodes": nodes,
        "connections": {**chain("Every Monday 08:00", "Request reports"), **chain("Run now", "Request reports")},
        "settings": {"executionOrder": "v1", "timezone": "UTC"},
        "meta": {"description": "Asks the agent to build and deliver each tenant's weekly report (Tier 2)."},
        "pinData": {},
        "active": False,
        "tags": [],
    }


WORKFLOWS = {
    "helpdesk-approvals.json": lambda: webhook_workflow(
        "Help desk: approvals and provisioning", "helpdesk-approvals", "verify_agent.js", "handle_approvals.js",
        "Agent -> n8n (Tier 1): approval cards, group provisioning, denial notices."),
    "helpdesk-notifications.json": lambda: webhook_workflow(
        "Help desk: notifications", "helpdesk-notify", "verify_agent.js", "handle_notifications.js",
        "Agent -> n8n: outage/escalation alerts, SOP drafts for review, weekly reports."),
    "helpdesk-slack-actions.json": lambda: webhook_workflow(
        "Help desk: Slack button clicks", "helpdesk-slack-actions", "verify_slack_action.js",
        "handle_slack_action.js",
        "Slack interactivity -> signed callbacks to the agent (approvals, SOP draft review)."),
    "helpdesk-weekly-reports.json": schedule_workflow,
}


def render() -> dict[str, str]:
    return {fname: json.dumps(build(), indent=2, ensure_ascii=False) + "\n" for fname, build in WORKFLOWS.items()}


def main() -> int:
    rendered = render()
    if "--check" in sys.argv[1:]:
        stale = [f for f, text in rendered.items() if not (OUT / f).exists() or (OUT / f).read_text() != text]
        if stale:
            print(f"stale n8n workflows (run build_workflows.py): {', '.join(stale)}")
            return 1
        print(f"n8n workflows up to date ({len(rendered)})")
        return 0
    OUT.mkdir(exist_ok=True)
    for fname, text in rendered.items():
        (OUT / fname).write_text(text)
    print(f"wrote {len(rendered)} workflows to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
