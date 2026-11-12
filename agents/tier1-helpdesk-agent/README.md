# Tier 1 IT Help Desk Agent

A FastAPI service that triages IT tickets from Slack, Teams, Jira (or any system via
`/v1/triage`). It answers Tier 1 issues from the client's own SOPs, and **politely declines
anything outside Tier 1**.

```
ticket ─► budget check ─► SOP retrieval (Supabase pgvector, tenant-filtered in SQL)
       ─► Claude Haiku 5.5 triage ──(risky?)──► Claude Sonnet 5.5 second pass
       ─► deterministic guardrails ─► reply to user  (+ Tier 2 handoff if escalated)
```

## How out-of-scope requests are denied (cordially)

The model classifies every ticket into one of three scopes. Code, not the model, decides what
the user sees:

| Scope | Examples | What the user gets |
|---|---|---|
| `in_scope` | password reset, VPN, printer, Outlook, approved software, phishing report | SOP-grounded steps |
| `beyond_tier1` | firewall change, admin rights, someone else's account, backup restore | Fixed courteous "this needs our Tier 2 team" note + escalation |
| `not_it_support` | HR/payroll, legal, essays, homework, trivia, "ignore your instructions…" | Fixed courteous decline. No part of the request is answered |

Layers that keep it that way:

1. **Explicit scope policy** in the system prompt (`app/services/llm_router.py`).
2. **Untrusted-input fencing**: ticket text is HTML-escaped inside `<ticket>` and labelled as
   data, so "ignore previous instructions" can't close the tag or change the rules.
3. **Template replies for declines** (`app/services/guardrails.py`): out-of-scope replies are
   never model-written, so tone is always consistent and nothing out of scope leaks through.
4. **Second opinion**: an unsure decline (confidence < 0.80) is re-checked by Sonnet, so real
   IT problems aren't turned away.
5. **Refusals** (`stop_reason: "refusal"`) become the same courteous decline.
6. **Fail closed**: API errors, truncated output, a Supabase outage, or the monthly budget cap
   all route the ticket to a human with a polite holding message.

Other guardrails: cited SOP IDs must be ones actually retrieved; security incidents, low
confidence and uncited answers always escalate (security is floored at P2); escalated tickets
send the user a handoff note, and the unverified draft goes only to the technician.

## Layout

```
app/
  main.py                 FastAPI routes: /health, /v1/triage, /webhooks/{slack,teams,jira}
  config.py               Pydantic BaseSettings (env vars, token caps, prices, budget)
  schemas/ticket.py       IncomingTicketWebhook, AgentTriageResult, LLMTriageOutput
  services/
    rag.py                Voyage embeddings + match_sop_chunks RPC
    llm_router.py         Scope policy prompt, Haiku/Sonnet routing, structured output
    guardrails.py         Deterministic policy + courteous reply templates
    pipeline.py           budget -> RAG -> router -> guardrails
    usage.py              Token cost ledger + MONTHLY_TOKEN_BUDGET_USD cap
    ticketing.py          Slack / Teams / Jira signature checks and replies
    supabase_client.py    Thin async PostgREST client
ingest/sync_docs.py       Chunk, embed and sync SOPs (incremental, with --prune)
supabase/migrations/      pgvector table, tenant-filtered match function, usage ledger
terraform/main.tf         Zero-NAT ECS Fargate (ARM64) + ALB/HTTPS + Secrets Manager
Dockerfile                Multi-stage ARM64 image, non-root
```

## Run locally

```bash
pip install -r requirements-dev.txt
python -m pytest -q                       # 31 offline tests, no API calls

cp .env.example .env                      # fill in keys
# Apply supabase/migrations/001_tier1_agent.sql to your Supabase project, then:
python -m ingest.sync_docs --tenant acme-corp --docs ingest/sample_sops
uvicorn app.main:app --reload

curl -s localhost:8000/v1/triage -H "x-webhook-secret: $WEBHOOK_SECRET" \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"acme-corp","ticket_id":"T-1","reporter_email":"pat@acme.com",
       "subject":"VPN","body":"GlobalProtect says portal not found","source_system":"zendesk"}'
```

## Deploy

```bash
cd terraform
terraform init
terraform apply -var client_slug=acme-corp -var acm_certificate_arn=arn:aws:acm:... \
  -var supabase_url=... -var supabase_service_key=... -var llm_api_key=... \
  -var voyage_api_key=... -var webhook_secret=... \
  -var 'integration_secrets={SLACK_BOT_TOKEN="xoxb-...",SLACK_SIGNING_SECRET="..."}'

# Push the image (Graviton needs linux/arm64)
aws ecr get-login-password | docker login --username AWS --password-stdin <ecr_repository_url>
docker buildx build --platform linux/arm64 -t <ecr_repository_url>:latest --push ..
aws ecs update-service --cluster acme-corp-cluster --service acme-corp-tier1-agent-service --force-new-deployment
```

Point a DNS name on your certificate at `alb_dns_name`, then register these webhook URLs:

| Platform | URL | Setup |
|---|---|---|
| Slack | `https://<host>/webhooks/slack` | Events API: `app_mention`, `message.im`. Scopes: `chat:write`, `users:read.email` |
| Teams | `https://<host>/webhooks/teams` | Outgoing webhook; put its security token in `TEAMS_OUTGOING_WEBHOOK_SECRET` |
| Jira | `https://<host>/webhooks/jira` | Webhook on *Issue created* with a secret (`JIRA_WEBHOOK_SECRET`) |
| Other | `https://<host>/v1/triage` | Send header `x-webhook-secret` |

Secrets go into Terraform state, so use an encrypted remote backend (S3 + KMS), or put the
values in Secrets Manager yourself.

## Cost notes

* No NAT Gateway: tasks use public IPs with ALB-only ingress. The ALB (about $16-20/month) is
  what gives Slack/Teams/Jira the stable HTTPS endpoint they require.
* Most tickets cost a fraction of a cent on Haiku 5.5. Sonnet 5.5 runs only on risky tickets.
* `MONTHLY_TOKEN_BUDGET_USD` (default $25) is enforced from the `llm_usage` ledger. Once it's
  hit, tickets go to humans until the month rolls over.
* The Sonnet route opts in to server-side refusal fallbacks (`fallbacks: "default"`). Turn it
  off with `ENABLE_REFUSAL_FALLBACKS=false`.
