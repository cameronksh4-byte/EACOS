# Tier 1 IT Help Desk Agent

A multi-tenant Tier 1 IT help desk agent for managed service providers. It triages tickets from
Slack, Teams, Jira or any system that can POST JSON, answers them from each client's own SOPs,
and **politely declines anything outside Tier 1**.

One codebase, three commercial tiers, chosen by one environment variable:

| Tier | `SERVICE_TIER` | What's switched on |
|---|---|---|
| 0: Incentive (free 30-day trial) | `TIER_0_INCENTIVE` | Triage + draft, screenshot vision, 30-minute outage burst detection, ROI telemetry |
| 1: Production (paid build) | `TIER_1_PRODUCTION` | + live identity/MDM context, multi-turn slot-filling, n8n manager approvals |
| 2: Managed Ops (retainer) | `TIER_2_MANAGED_OPS` | + closed-ticket-to-SOP knowledge flywheel, weekly executive ROI/gap report |

Tiers are cumulative. `DISABLED_FEATURES` can switch individual features off (a kill switch), but
nothing can switch on a feature above the tier. Routes for locked features return **404**.

```
ticket ─► trial check ─► [T1] conversation state ─► embed ─► outage burst? ──yes──► broadcast (no model call)
        ─► screenshot vision ─► [T1] identity/MDM ─► SOP search (tenant + OS filter)
        ─► Haiku 5.5 ─► (risky?) Sonnet 5.5          every call budget-reserved first
        ─► [T1] clarify (≤2, DB-capped) │ catalog → provision / approval card / escalate
        ─► guardrails + result validator ─► reply  (+ Tier 2 notification, telemetry)
```

## Guarantees

These are enforced in code and the database, not left to the model, and are checked by
`tests/test_tiered_agent.py` (exhaustively where the input space allows).

1. **No invented policy.** Troubleshooting steps reach a user only when they cite `doc_id`s
   actually retrieved from the tenant's pgvector SOPs. Invented citations are stripped.
2. **Forced escalation.** `requires_tier2_escalation = True` whenever confidence < 0.80, the
   category is `security_incident`, or nothing is cited. The only exceptions are four outcomes
   that give no troubleshooting steps, and each applies only while its control exists:

   | Outcome | Covered by | Without the control |
   |---|---|---|
   | Clarifying question | 2-question cap spent atomically in the DB | escalates |
   | Waiting for manager approval | approval record (expires → thread escalates) | escalates |
   | Courteous out-of-scope decline | fixed template; no steps or citations | n/a |
   | Outage broadcast | parent incident delivered to Tier 2 (retried until it is) | escalates |

   Security incidents escalate whatever the outcome. Every override is recorded in `guardrail_overrides`.
3. **Hard monthly spend cap per tenant.** Every model call (vision, triage, SOP synthesis)
   reserves its worst-case cost under a per-tenant lock before it runs, then settles to the
   real cost. If the cap would be exceeded, or the ledger can't be reached to verify it, the call
   isn't made and the ticket goes to a person.

Out-of-scope requests are classified as `beyond_tier1` (IT work needing Tier 2+, which is declined and
escalated) or `not_it_support` (HR, legal, homework, "ignore your instructions", which is declined).
Both use fixed, courteous templates, never model-written text.

## API

| Route | Auth | Tier |
|---|---|---|
| `GET /health` | none | all |
| `POST /v1/triage` | `x-webhook-secret` | 0+ |
| `GET /v1/telemetry/{tenant_id}/roi-summary?days=30` | `x-webhook-secret` | 0+ |
| `POST /webhooks/slack` · `/webhooks/teams` · `/webhooks/jira` | platform signature | 0+ |
| `POST /v1/callbacks/approval` | n8n signature | 1+ |
| `POST /v1/webhooks/ticket-resolved` | `x-webhook-secret` | 2 |
| `POST /v1/callbacks/sop-draft` | n8n signature | 2 |
| `POST /v1/reports/weekly?tenant_id=…` | `x-webhook-secret` | 2 |
| `POST /v1/webchat/message` · `/v1/webchat/grants` · `/v1/webchat/account` | website signature | any, when `WEBCHAT_ENABLED` |

There is no `/docs` or `/openapi.json`: a schema would list routes this deployment's tier hides.

## Website free trial

Off by default. With `WEBCHAT_ENABLED=true`, the company website can offer the help desk to
visitors: 5 free questions, then sign-up and a one-time 10-question trial, then credit packs or
a monthly plan with an allowance. The website's server signs every request; browsers never reach this
API. Each question is counted in the database before any model call, answered from the chat's own
tenant and SOPs in sandbox mode (no threads, outage detection, approvals or Tier 2 pages), and
spends against its own hard monthly budget. Payments are Stripe Checkout on the website; its signed
webhook tells this API what was bought, once per payment. Contract, website code (chat, sign-up,
Stripe) and the hand-off prompt for the website build are in `integrations/website/`.

## n8n contract

**Signatures** (both directions): header `X-Agent-Signature: t=<unix>,v1=<hex>`, where
`v1 = HMAC-SHA256(secret, "<t>." + raw_body)`. Receivers reject anything more than 5 minutes
old. The agent signs with `N8N_OUTBOUND_SIGNING_SECRET`. n8n signs callbacks with `N8N_CALLBACK_SECRET`.

Events the agent POSTs:

| Event | To | Purpose |
|---|---|---|
| `approval.requested` | `N8N_APPROVAL_WEBHOOK_URL` | Render a Slack/Teams card with [Approve]/[Deny]. Echo `approval_id` + `callback_nonce` to `callback_url`. |
| `provisioning.requested` | `N8N_APPROVAL_WEBHOOK_URL` | Add `requester_email` to `provisioning_group` in Entra ID / Okta. |
| `approval.denied` | `N8N_APPROVAL_WEBHOOK_URL` | Tell the requester. |
| `outage.detected` · `thread.escalated` | `N8N_NOTIFY_WEBHOOK_URL` (or Slack) | Page technicians. Sent for every result that requires Tier 2, on every channel. |
| `sop_draft.created` | `N8N_NOTIFY_WEBHOOK_URL` | Send the draft to the IT manager with [Ingest]/[Reject]. |
| `report.weekly` | `N8N_NOTIFY_WEBHOOK_URL` | Deliver the executive report (Markdown included). |

Callbacks n8n POSTs (signed):

```jsonc
// /v1/callbacks/approval
{"approval_id": "…", "callback_nonce": "…", "tenant_id": "acme-corp",
 "decision": "approved", "decided_by_email": "boss@acme.com", "decision_note": null}
// /v1/callbacks/sop-draft
{"tenant_id": "acme-corp", "draft_id": "…", "decision": "approve", "reviewer_email": "it@acme.com"}
```

**n8n must answer after the work is done.** The agent records a 2xx as "delivered" (it stops
retrying, and outage tickets skip their own escalation), so the bundled workflows respond 200
only once Slack or the directory accepted the request, and 502 otherwise.

An approval is accepted once, only from the designated approver, only with the nonce, and
before expiry. Replays and wrong approvers get 409. Schedule `POST /v1/reports/weekly` from an
n8n Cron node.

## Setup

```bash
make install                 # Python 3.12
cp .env.example .env         # fill in keys

# Database: apply the migration in the Supabase SQL editor or with `supabase db push`
#   supabase/migrations/001_init_schema.sql
python -m ingest.sync_docs --tenant acme-corp --docs ingest/sample_sops    # load the client's SOPs
python -m ingest.sync_docs --tenant shared-kb --docs knowledge_base/sops --prune  # baseline KB (once per project)

make run
curl -s localhost:8000/v1/triage -H "x-webhook-secret: $WEBHOOK_SECRET" -H 'content-type: application/json' \
  -d '{"tenant_id":"acme-corp","ticket_id":"T-1","reporter_email":"pat@acme.com",
       "subject":"VPN","body":"GlobalProtect says portal not found","source_system":"api"}'
```

SOP files are Markdown with frontmatter: `doc_id`, `title`, `system_name`,
`os_platform` (`any|windows|macos|linux|ios|android|chromeos`), and `last_verified_date`.
For Tier 1 approvals, fill the `app_catalog` table (item, aliases, whether approval is needed,
`approver_role`, `provisioning_group`, `doc_id`). Whether something needs approval is decided by
this table, never by the model.

### Baseline knowledge base

`knowledge_base/sops/` is a help-desk knowledge base every tenant searches alongside its own SOPs.
It is loaded once into its own tenant (`SHARED_KB_TENANT_ID`, default `shared-kb`, which can never be
a client or the website tenant), so updating it reaches every client at once.

- `cases/` — 30 common Tier 1 cases (email, printing, network, VPN, accounts, performance, apps,
  files, hardware, mobile, security), generated from
  `knowledge_base/source/IT_Help_Desk_RAG_Knowledge_Base.html` by `python -m ingest.build_shared_kb`.
  The page is read as data only (its script is never run); a test fails if the Markdown and the page
  drift apart, and the deploy refuses to load a stale copy.
- `escalation/` — 15 must-escalate triggers (security, PHI, hardware failure, outages…), also generated.
- `policy/` — hand-written: guardrails, the handoff packet, onboarding/offboarding, field notes.

Ranking: when a client SOP and a baseline SOP match about equally (within 0.03), the client's wins.
The model sees each SOP labelled `source="client"` or `source="baseline"` and must not apply
product-specific baseline steps (a VPN client, an MDM, a portal) unless the ticket shows that product.

Any SOP whose `doc_id` starts with `KB-ESC-` is **escalate-only**: the agent may give the first-aid
steps it contains, but citing it always alerts a person (enforced in `AgentTriageResult`, not just in
the prompt). The reply tells the user who will follow up through `TIER2_CONTACT_HINT`, so set that
per client to a real team or phone number.

Open points carried over from the source page, to confirm before relying on them: the 2026 HIPAA
Security Rule update is a proposed rule, not final; the CISA/FTC phishing wording and the vendor
forum links were not verified.

Identity (Tier 1): `IDENTITY_PROVIDER=microsoft_graph` needs app permissions `User.Read.All` and
`DeviceManagementManagedDevices.Read.All`. `AuditLog.Read.All` (Entra ID P1+) is optional, for the
failed sign-in count. `okta` needs an API token with read access to users and the System Log.
`directory_cache` reads the `tenant_directory` table.

## Deploy

**Start here: [`deploy/DEPLOY.md`](deploy/DEPLOY.md).** It covers one config file and one command:

```bash
cp deploy/deploy.env.example deploy/acme-corp.env && chmod 600 deploy/acme-corp.env   # fill it in
deploy/deploy.sh deploy/acme-corp.env --plan     # validate + dry run
deploy/deploy.sh deploy/acme-corp.env            # build, deploy, load SOPs, smoke test
```

The script creates encrypted remote state, applies the Supabase schema, pushes an immutable
linux/arm64 image (tag = git commit), applies Terraform (zero-NAT ECS Fargate on Graviton, ALB +
ACM with optional Route 53 automation, Secrets Manager, CPU autoscaling, optional alarms and WAF),
waits for the service, loads SOPs and the catalog, imports the n8n workflows, and smoke-tests over
HTTPS. To have an AI coding agent run it with approval gates, use
[`deploy/DEPLOY_PROMPT.md`](deploy/DEPLOY_PROMPT.md).

**Upgrading a client** means editing `SERVICE_TIER` (plus that tier's settings) and re-running. A
missing setting fails at `--plan`, before anything changes.

## Integrations

- **Slack:** `integrations/slack/app-manifest.yaml` (events → agent, button clicks → n8n).
- **Website:** `integrations/website/` holds the free-trial contract, the Netlify function and a
  widget for the company site, with tests.
- **n8n:** `integrations/n8n/` holds 4 importable workflows (approval cards + Entra ID/Okta
  provisioning, Slack button handling, notifications, weekly reports), generated from tested JS
  sources and verified end to end inside n8n 2.47.5.
- **Tier 1 data:** `python -m ingest.load_tenant_data --tenant … --catalog … --directory …`
  (samples in `ingest/`).

## Testing

```bash
make test       # Python tests, offline (fake Supabase/Claude/Voyage/n8n), no API spend
make test-sql   # migration + SQL behaviour checks against a scratch Postgres+pgvector (SCRATCH_DB_URL)
make lint
cd terraform && terraform test                        # plan-only tests with a mocked AWS provider
node --test integrations/n8n/test/handlers.test.js   # n8n Code-node logic
node --test integrations/website/test/*.test.mjs     # website free trial: chat, sign-up, Stripe
python integrations/n8n/test/e2e_n8n.py              # optional: inside real n8n (Node >= 24)
```

CI (`.github/workflows/tier1-helpdesk-agent-tests.yml`) runs the Python, SQL, Terraform and n8n
checks, shellcheck on the deploy script, and a real linux/arm64 image build with a smoke test
under QEMU.

## Operations

- **Background loop** (every minute): retries owed Tier 2 notifications. Every 5 minutes it
  also runs `run_housekeeping()` (expires approvals and escalates their threads, resolves stale
  outages Tier 2 already knows about, prunes `recent_tickets`). Every worker runs the loop, and a
  database lease lets one of them take each tick, so a notice is retried once, not once per worker.
- **Costs**: most tickets cost a fraction of a cent on Haiku 5.5. Sonnet 5.5 runs only on risky
  tickets. Outage duplicates cost one embedding and no model tokens. ROI credits 15 minutes only
  to tickets resolved with cited steps (outage broadcasts are reported separately).
- **Dependencies**: `requirements.in` lists ranges. `make lock` re-resolves `requirements.txt` for
  Python 3.12 on linux/arm64. The base image is pinned by tag and digest.

## Layout

```
app/
  main.py                  routes, lifespan, maintenance loop
  config.py                ServiceTier, Feature flags, settings, startup validation
  gating.py                require_feature (routes), @gated (services), signature checks
  schemas/ticket.py        all contracts; AgentTriageResult enforces guardrail #2
  services/
    pipeline.py            the per-ticket flow above
    llm_client.py          budget-gated, schema-constrained Claude calls (Anthropic or Bedrock)
    llm_router.py          tier-aware prompt, Haiku → Sonnet routing
    guardrails.py          policy + every user-facing template
    vision.py              SSRF-safe screenshot fetching and extraction
    outage.py              30-minute burst detection, Tier 2 incident notification
    rag.py                 Voyage embeddings, tenant/OS-filtered SOP search
    telemetry.py           ROI telemetry and summary
    context_enrichment.py  Graph / Okta / cache → UserContext
    conversation.py        threads, slots, capped clarifications, escalation notices
    approvals.py           catalog, approval cards, callbacks, provisioning
    flywheel.py            resolved ticket → sanitized SOP draft → one-click ingest
    reports.py             weekly executive report
    webchat.py             public website chat: metering, sandboxed answers, plans and credits
    chunking.py, notifier.py, signing.py, background.py, supabase_client.py, ticketing.py
ingest/                    SOP ingest CLI, catalog/directory loader, samples, build_shared_kb
knowledge_base/            baseline KB shared by every tenant (source page + generated SOPs)
deploy/                    deploy.sh, deploy.env.example, DEPLOY.md, DEPLOY_PROMPT.md, smoke test
integrations/              Slack app manifest, n8n workflows, website free trial (+ sources and tests)
supabase/migrations/       schema, RLS, SQL functions; supabase/tests/ = SQL checks
terraform/                 infrastructure + plan tests
scripts/lock_requirements.py
```
