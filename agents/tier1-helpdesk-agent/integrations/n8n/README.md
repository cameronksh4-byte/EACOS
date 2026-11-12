# n8n workflows

Four workflows connect the agent to Slack and to Entra ID / Okta. Tier 0 needs none of them
(it can alert a Slack channel directly). Tier 1 needs the first three. Tier 2 needs all four.

| File | Webhook path | Does |
|---|---|---|
| `helpdesk-approvals.json` | `/webhook/helpdesk-approvals` | DMs the approver a card with [Approve]/[Deny], adds approved users to the item's Entra ID / Okta group, tells requesters about denials |
| `helpdesk-slack-actions.json` | `/webhook/helpdesk-slack-actions` | Slack's interactivity URL: turns button clicks into signed callbacks to the agent, then updates the message |
| `helpdesk-notifications.json` | `/webhook/helpdesk-notify` | Outage and escalation alerts, SOP drafts for review with [Ingest]/[Reject], weekly reports |
| `helpdesk-weekly-reports.json` | (schedule, Mondays 08:00 UTC) | Asks the agent to build and deliver each tenant's weekly report |

Every inbound request is verified (the agent's HMAC signature, or Slack's), and anything
unverified gets 401 with no side effects. The clicker's identity comes from Slack, never
from the button, and the agent only accepts a decision from the designated approver.

## Install

1. **Import**: in n8n, *Workflows → Import from File* for each JSON in `workflows/`, then
   *Publish* each one. Or let the deploy script do it through the n8n API:
   `N8N_API_URL=… N8N_API_KEY=… python integrations/n8n/deploy_workflows.py`
2. **Set environment variables on the n8n instance** (then restart n8n):

| Variable | Value |
|---|---|
| `N8N_BLOCK_ENV_ACCESS_IN_NODE` | `false` (the workflows read the settings below) |
| `NODE_FUNCTION_ALLOW_BUILTIN` | `crypto` (signature checks) |
| `N8N_AGENT_SIGNING_SECRET` | the agent's `N8N_OUTBOUND_SIGNING_SECRET` |
| `N8N_CALLBACK_SECRET` | the agent's `N8N_CALLBACK_SECRET` |
| `AGENT_BASE_URL` | `https://<public_hostname>` |
| `AGENT_WEBHOOK_SECRET` | the agent's `WEBHOOK_SECRET` (weekly reports) |
| `AGENT_TENANTS` | comma-separated tenant IDs for weekly reports, e.g. `acme-corp` |
| `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` | from the Slack app (`integrations/slack/app-manifest.yaml`) |
| `SLACK_ALERT_CHANNEL` | channel ID for outages, escalations, manual-provisioning alerts |
| `SLACK_SOP_REVIEW_CHANNEL` | channel ID where the IT manager reviews SOP drafts (Tier 2) |
| `SLACK_EXEC_CHANNEL` | channel ID for the weekly executive report (Tier 2) |
| `PROVISIONING_PROVIDER` | `entra`, `okta`, or `none` (= post a manual-provisioning alert) |
| `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET` | `entra`: app with `GroupMember.ReadWrite.All` and `User.Read.All` |
| `OKTA_DOMAIN`, `OKTA_API_TOKEN` | `okta`: token allowed to manage group membership |

Invite the bot to the three channels. `provisioning_group` in the agent's `app_catalog` is the
Entra ID group object ID or the Okta group ID.

**Security note:** with env access enabled, anyone who can edit workflows on this n8n instance
can read these values. Use an n8n instance dedicated to the help desk, or limit editor access.

## Develop

The Code-node logic lives in `src/*.js` (`lib.js` is prepended to each). After editing:

```bash
python integrations/n8n/build_workflows.py        # regenerate workflows/*.json
node --test integrations/n8n/test/handlers.test.js
# Full run inside real n8n (Node >= 24, n8n installed) against mock Slack + agent:
N8N_BIN=…/node_modules/n8n/bin/n8n NODE_BIN=…/node python integrations/n8n/test/e2e_n8n.py
```

The end-to-end run was last verified on n8n 2.47.5: 9 checks covering card delivery,
signature rejection in both directions, approve/ingest clicks producing callbacks the agent
accepts, notification routing, and weekly reports per tenant.
