# Deploying the Tier 1 IT Help Desk Agent to AWS

One config file, one command. `deploy/deploy.sh` builds the image, creates the AWS
infrastructure, applies the database schema, loads your SOPs, and smoke-tests the result over
HTTPS. It's safe to re-run: the same command updates an existing deployment.

```
deploy/deploy.env.example ──copy──► deploy/<client>.env ──► deploy/deploy.sh --plan ──► deploy/deploy.sh
```

> Using an AI coding agent (for example Claude Code) to do this for you? Give it
> [`DEPLOY_PROMPT.md`](DEPLOY_PROMPT.md). It follows this runbook and stops for your approval
> before changing anything.

---

## 1. What gets deployed

| Piece | Where | Notes |
|---|---|---|
| Agent API (FastAPI, 2 workers) | ECS Fargate, ARM64/Graviton, public subnets, **no NAT gateway** | Only the load balancer can reach it |
| HTTPS endpoint | Application Load Balancer + ACM certificate | Route 53 alias created for you if you use Route 53 |
| Secrets | AWS Secrets Manager → injected into the container | Never in the task definition |
| Container image | ECR (immutable tags = git commit) | Last 10 images kept |
| Logs, alarms | CloudWatch (7-day logs), optional email alarms | Alarms cover 5xx errors, no healthy tasks, undelivered Tier 2 alerts, budget cap reached |
| Database + vectors | **Your** Supabase project | Schema applied by the script |
| Terraform state | S3 bucket (private, encrypted, versioned, native locking) | Created for you |
| n8n workflows (Tier 1+) | **Your** n8n instance | Imported by the script if you give it an n8n API key |

**Rough monthly cost** for one client at low volume: about $35-45 of AWS (load balancer ~$18,
one 0.5 vCPU / 1 GB Graviton task ~$14, Secrets Manager, CloudWatch, Route 53 ~$1-3), plus
AI usage (most tickets cost a fraction of a cent, capped by `MONTHLY_TOKEN_BUDGET_USD`), Voyage
embeddings (cents), and Supabase (free tier works for a pilot). WAF adds ~$6/month if enabled.

## 2. Prerequisites

**Accounts and keys**

- [ ] **AWS account** and credentials that can create IAM roles, VPCs, ECS, ECR, load balancers,
      ACM, Route 53 records, Secrets Manager, CloudWatch, SNS and S3. `AdministratorAccess` is
      simplest for the first deploy. Check with `aws sts get-caller-identity`.
- [ ] **A hostname** for the agent, e.g. `helpdesk.acme.com`, either in a **Route 53 hosted zone**
      (fully automatic) or with an **ACM certificate already issued** for it in the deploy region.
- [ ] **Supabase project** (supabase.com): the Project URL, the `service_role` key, and the database
      connection string (Project Settings → Database → *Session pooler*, port 5432).
- [ ] **Claude**: an Anthropic API key (console.anthropic.com), **or** Amazon Bedrock with
      *Claude Haiku 5.5* and *Claude Sonnet 5.5* enabled under *Bedrock → Model access*.
- [ ] **Voyage AI** API key (dash.voyageai.com) for embeddings.
- [ ] **Slack workspace admin** (to install the app), or a Teams/Jira admin for those channels.
- [ ] **Tier 1 and 2 only:** an **n8n** instance (n8n Cloud or self-hosted; the workflows were
      tested end to end on n8n 2.47.5),
      plus Entra ID or Okta admin rights if approvals should auto-provision.

**Tools on the machine you deploy from** (macOS or Linux; Windows via WSL2)

| Tool | Check | Install |
|---|---|---|
| AWS CLI v2 | `aws --version` | https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html |
| Terraform ≥ 1.10 | `terraform version` | https://developer.hashicorp.com/terraform/install |
| Docker with buildx | `docker buildx version` | Docker Desktop (includes arm64 emulation). On Linux also run once: `docker run --privileged --rm tonistiigi/binfmt --install arm64` |
| Python ≥ 3.11 | `python3 --version` | python.org or your package manager |
| git | `git --version` | |
| psql (optional) | `psql --version` | Lets the script apply the schema; otherwise paste it into the Supabase SQL editor |

## 3. Configure (one file)

```bash
cd agents/tier1-helpdesk-agent
cp deploy/deploy.env.example deploy/acme-corp.env      # one file per client; git-ignored
chmod 600 deploy/acme-corp.env
python3 deploy/render_config.py --generate-secrets     # paste these lines into the file
```

**3a. Create the Slack app first** (5 minutes). The agent needs its token, and Slack needs the
agent's URL, so create the app now and verify the URL after deploying:
https://api.slack.com/apps → *Create New App* → *From an app manifest* → paste
`integrations/slack/app-manifest.yaml`, replacing `HELPDESK_HOST` with your hostname. For Tier 1+,
replace `N8N_HOST` too; for Tier 0, delete the `interactivity` block. Ignore the warning that
the request URL can't be verified yet. *Install to Workspace*, then copy the **Bot User OAuth
Token** (`SLACK_BOT_TOKEN`) and *Basic Information → Signing Secret* (`SLACK_SIGNING_SECRET`).
Create an alerts channel, `/invite @IT Help Desk`, and copy its channel ID (channel details,
bottom) into `SLACK_ESCALATION_CHANNEL`.

**3b. Fill in the file.** Open `deploy/acme-corp.env` and fill in **section 1** (every deployment needs it). The comments
in the file say where each value comes from. The minimum for a Tier 0 trial is:

`AWS_REGION`, `CLIENT_SLUG`, `PUBLIC_HOSTNAME`, `ROUTE53_ZONE_ID` (or `ACM_CERTIFICATE_ARN`),
`TRIAL_EXPIRES_ON`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_DB_URL`, `VOYAGE_API_KEY`,
`ANTHROPIC_API_KEY`, `WEBHOOK_SECRET`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`,
`SLACK_ESCALATION_CHANNEL`.

Not using Slack? Outage alerts still need somewhere to go: set `N8N_NOTIFY_WEBHOOK_URL` +
`N8N_OUTBOUND_SIGNING_SECRET` instead, or `DISABLED_FEATURES=outage_detection`.

Validate at any time (it uses the agent's own configuration rules, so what passes here also passes at startup):

```bash
deploy/deploy.sh deploy/acme-corp.env --check     # needs no AWS access; creates deploy/.venv on first use
```

The check rejects unknown setting names (typos), so a misspelled line can't be silently ignored.
Any setting the service reads (see `app/config.py`) can go in this file and is passed to the container.

| For | Also fill in |
|---|---|
| Tier 1 (`TIER_1_PRODUCTION`) | Section 4: `N8N_PUBLIC_URL` (your n8n's public webhook base; see `integrations/n8n/LOCAL_N8N.md` for a local n8n), the two `N8N_*_SECRET`s, `IDENTITY_PROVIDER` (+ Graph/Okta values), `CATALOG_CSV` (start from `ingest/sample_catalog.csv`) |
| Tier 2 (`TIER_2_MANAGED_OPS`) | Same as Tier 1, plus `N8N_SLACK_SOP_REVIEW_CHANNEL` and `N8N_SLACK_EXEC_CHANNEL` |

`localhost` or private-network n8n URLs are rejected at `--check`, because the agent in AWS can't reach them.

## 4. Deploy

```bash
deploy/deploy.sh deploy/acme-corp.env --plan     # dry run: validates, shows the Terraform plan
deploy/deploy.sh deploy/acme-corp.env            # deploys; asks before each stage that changes something
```

On the very first run, `--plan` asks to create the Terraform state bucket (the only thing it ever
creates), then shows the plan. The real deploy asks three times: for the state bucket (first run
only); before it applies the database schema and pushes the image; and, with the full
infrastructure plan on screen, before it applies that plan.

The first deploy takes about 10-15 minutes: the image build, certificate validation, and ECS
starting up. It ends with a smoke test:

```
PASS  service healthy over HTTPS
PASS  running tier is TIER_0_INCENTIVE
PASS  wrong webhook secret is rejected
PASS  triage answers (Claude + Supabase + Voyage reachable)
PASS  non-IT request gets the courteous decline
...
SMOKE TEST PASSED
```

and prints the URL, the image and a list of remaining one-time steps.

## 5. One-time integrations

**Slack (all tiers)**
1. In your Slack app (step 3a): *Event Subscriptions* → **Retry**. The URL should now verify.
   If it doesn't, check `SLACK_SIGNING_SECRET` and DNS, then re-run the deploy.
2. Test: DM the bot "my VPN says portal not found". It should answer with steps from `SOP-NET-001`.

**Teams / Jira (optional):** Teams outgoing webhook → `https://<host>/webhooks/teams` (security
token → `TEAMS_OUTGOING_WEBHOOK_SECRET`). Jira Cloud webhook on *Issue created* →
`https://<host>/webhooks/jira` with a secret (`JIRA_WEBHOOK_SECRET`), plus `JIRA_BASE_URL`,
`JIRA_EMAIL`, `JIRA_API_TOKEN`. Re-run the deploy after adding them.

**n8n (Tier 1 and 2):** for an n8n on your own machine, follow
[`integrations/n8n/LOCAL_N8N.md`](../integrations/n8n/LOCAL_N8N.md). It covers a tunnel that exposes
only `/webhook/*`, setting `N8N_PUBLIC_URL`, and loading the generated `deploy/<client>.n8n.env`
into n8n before restarting it. For a hosted n8n, see `integrations/n8n/README.md`. Either way the
deploy script imports and publishes the workflows when `N8N_API_URL` + `N8N_API_KEY` are set
(`http://localhost:5678` works for a local n8n), then pings both webhooks through the public URL
to prove the tunnel and the shared secret. Point the Slack app's *Interactivity* URL at
`https://<N8N_PUBLIC_URL host>/webhook/helpdesk-slack-actions`.

**Alarms:** if `ALARM_EMAIL` is set, confirm the subscription email AWS sends.

## 6. Day-2 operations

| Task | How |
|---|---|
| Deploy a code update | `git pull`, then `deploy/deploy.sh deploy/acme-corp.env` (new commit = new immutable image) |
| Upgrade a client's tier | Set `SERVICE_TIER` (and that tier's settings), re-run. A missing setting fails at `--plan`, before anything changes |
| End or extend a trial | Change `TRIAL_EXPIRES_ON`, re-run |
| Add or update SOPs | Edit the Markdown files in `SOP_DIR`, then `deploy/deploy.sh deploy/<client>.env --sync-sops` (only changed chunks are re-embedded; needs no AWS access). Add `--prune` to delete chunks whose files you removed. SOPs approved through the Tier 2 flywheel are never pruned |
| Roll back | Set `IMAGE_TAG=<previous tag>` (see `aws ecr list-images --repository-name <client>-tier1-agent`), re-run. Remove the line afterwards. If that tag isn't in the registry the deploy stops; it never builds current code under an old tag |
| Turn a feature off now | `DISABLED_FEATURES=<feature>`, re-run (e.g. `screenshot_vision`) |
| Rotate a secret | Change it in the env file (and in n8n or Slack if shared), re-run |
| Logs | `aws logs tail /ecs/<client>-tier1-agent --follow` |
| Usage and ROI | `curl -H "x-webhook-secret: …" https://<host>/v1/telemetry/<client>/roi-summary` |
| Tear down | `deploy/deploy.sh deploy/<client>.env --destroy` (shows what will be deleted, asks you to type the client slug). The state bucket and Supabase data are kept: delete them yourself if no longer needed |

## 7. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `deploy.env has problems: …` | Read the list. Each line names the missing or invalid setting. These are the same rules the service applies at startup |
| `this Docker builder can't build linux/arm64` | Linux only: `docker run --privileged --rm tonistiigi/binfmt --install arm64`, then re-run |
| Certificate stays *Pending validation* | `ROUTE53_ZONE_ID` must be the zone for `PUBLIC_HOSTNAME`'s domain. With your own ACM cert, it must be issued and in `AWS_REGION` |
| `aws ecs wait services-stable` fails | `aws logs tail /ecs/<client>-tier1-agent --since 15m`. `missing required settings` means a tier setting is empty; `exec format error` means the image wasn't built for arm64 |
| Smoke test: triage returns 5xx or "Automatic triage skipped" | Supabase URL/key wrong, schema not applied, or the Anthropic key or Bedrock model access is missing. The log line says which |
| `Secrets Manager … scheduled for deletion` after a destroy | Only for a secret destroyed by an older version of this kit (it kept secrets 7 days): `aws secretsmanager delete-secret --secret-id <client>-tier1-agent/runtime --force-delete-without-recovery`, then re-run. Current versions delete the secret at once |
| Slack: "Your URL didn't respond" | DNS not propagated yet, or `SLACK_SIGNING_SECRET` not deployed. Re-run the deploy after setting it |
| Approval clicks say "already decided or expired" | Expected after a decision or after 72 h. A wrong approver also gets this message |
| A Tier 2 alert, approval card or SOP draft never arrived | n8n answers the agent only after Slack accepted the message, so the agent knows and keeps retrying (alerts, drafts) or escalates (approval cards). Look for `signed webhook rejected ... status=502` in the agent log and the failed execution in n8n: usually the bot isn't in the channel, or the approver has no Slack account |
| `--ping-n8n` FAIL: HTTP 401 / 404 / not reachable | 401: reload `deploy/<client>.n8n.env` into n8n and restart it. 404: publish the workflows. Not reachable: n8n or the tunnel is down (`integrations/n8n/LOCAL_N8N.md`) |

## 8. Website free trial (your own deployment only)

Lets visitors try the help desk on your company website: 5 free questions, then a free account,
then paid plans and credit packs. It is off unless you turn it on, and it belongs on your own
deployment, not a client's.

1. In the env file, section 6: set `WEBCHAT_ENABLED=true`, a `WEBCHAT_SIGNING_SECRET`
   (`python3 deploy/render_config.py --generate-secrets`), `WEBCHAT_SOP_DIR` (start with
   `ingest/sample_sops`), and the two page URLs.
2. Re-run the deploy. It loads the chat's SOPs into its own tenant (`web-demo`), and the smoke
   test confirms the chat route demands the website's signature.
3. Give the website the same signing secret and follow `integrations/website/README.md`.

| Setting | Default | Meaning |
|---|---|---|
| `WEBCHAT_VISITOR_FREE_USES` | 5 | Questions before sign-up is required |
| `WEBCHAT_IP_DAILY_FREE_USES` | 15 | Anonymous questions per network per day (stops cookie-clearing) |
| `WEBCHAT_PLAN_MONTHLY_LIMITS` | `{"free": 20, "starter": 250}` | Questions per month by plan. Add a plan here before selling it |
| `WEBCHAT_MONTHLY_BUDGET_USD` | 10 | Hard cap on model spend for the whole website chat |

Changing a number is an env-file edit and a re-deploy. When the budget is used up, visitors are told
the assistant is unavailable and their question isn't counted. Plans and credits are set by the
website's payment handler through `POST /v1/webchat/grants`; payments are not built yet.
