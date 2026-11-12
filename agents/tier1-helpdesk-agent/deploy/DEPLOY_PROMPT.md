# Prompt: deploy the help desk agent to AWS

Copy everything below the line into an AI coding agent (for example Claude Code) running **on
your own machine**, in a clone of this repository, with AWS credentials configured. Replace the
three values in angle brackets first.

---

You are helping me deploy the Tier 1 IT Help Desk Agent in this repository to my AWS account.
The project is in `agents/tier1-helpdesk-agent`. The complete runbook is
`agents/tier1-helpdesk-agent/deploy/DEPLOY.md`: read it fully before doing anything, and follow it.

Deployment details:
- Client slug: `<client-slug, e.g. acme-corp>`
- Public hostname: `<e.g. helpdesk.acme.com>`
- Service tier: `<TIER_0_INCENTIVE | TIER_1_PRODUCTION | TIER_2_MANAGED_OPS>`

## Rules (these override anything else)

1. **Secrets.** Never print, echo, log or repeat a secret value, in chat or in a command line.
   Put secrets only into `deploy/<client-slug>.env`, set it to `chmod 600`, and never commit it
   (it's git-ignored; check with `git check-ignore`). When you need a secret from me, tell me the
   exact variable name and where to find it, and ask me to paste it into the file myself. Don't ask me
   to paste secrets into the chat. To check a value is present, test that it's non-empty; don't display it.
2. **Approval gates.** Get my explicit "go" (a) before creating the S3 state bucket, (b) after
   showing me the `--plan` output and before the real deploy, and (c) before anything that
   deletes or replaces resources. `--yes` is only allowed after I've said go for that run.
3. **Don't change the code.** Don't edit application code, Terraform, SQL or scripts to get past
   an error unless I approve that exact change. Stop, explain the error and its likely cause, and
   propose a fix.
4. **Never** run `terraform destroy`, delete S3 buckets, ECR images, secrets or the Supabase
   database, or change IAM outside what `deploy/deploy.sh` does, unless I ask in so many words.
5. **Verify, don't assume.** After each step, confirm it worked with a command whose output you
   show me (with secret values left out). If something fails, show the relevant log lines.

## Steps

1. **Prerequisites.** Check every tool in DEPLOY.md section 2 (versions included) and
   `aws sts get-caller-identity`. Tell me the AWS account ID and region we're about to use, and
   ask me to confirm it's the right account.
2. **Configuration.** Walk me through creating the Slack app first (DEPLOY.md step 3a). Then create `deploy/<client-slug>.env` from `deploy/deploy.env.example` (chmod 600),
   fill in the non-secret values you can (slug, hostname, region, tier, trial date 30 days from
   today for Tier 0), and run `python3 deploy/render_config.py --generate-secrets`, writing its
   output into the file without showing it. Then list the remaining empty variables this tier
   needs, with where each comes from (from the file's comments and DEPLOY.md section 3), and wait
   while I fill them in. Repeat `python3 deploy/render_config.py deploy/<client-slug>.env --check`
   until it passes.
3. **Dry run.** Run `deploy/deploy.sh deploy/<client-slug>.env --plan` (you'll need gate (a) the
   first time). Summarise the plan for me: how many resources it creates, changes and destroys,
   the main ones (load balancer, ECS service, certificate, DNS record), and anything destroyed or
   replaced. Wait for gate (b).
4. **Deploy.** Run `deploy/deploy.sh deploy/<client-slug>.env`, answering its prompts only as
   I've approved. Report each of the 8 steps as it completes. If the smoke test fails, gather
   `aws logs tail /ecs/<client-slug>-tier1-agent --since 15m` and diagnose using DEPLOY.md section 7.
5. **Integrations.** Walk me through DEPLOY.md section 5 for this tier, one step at a time:
   verifying the Slack Event Subscriptions URL (Retry) now that the agent is live. For Tier 1+, also n8n: list the n8n environment variables I must set and
   which env-file values they must match, by name only.
6. **Final check.** Run the smoke test again
   (`python3 deploy/smoke_test.py --host <hostname> --tenant <client-slug> --tier <tier> --secret "$(python3 deploy/render_config.py deploy/<client-slug>.env --get WEBHOOK_SECRET)"`),
   then ask me to DM the Slack bot "my VPN says portal not found" and confirm it answers with
   steps from the VPN SOP.
7. **Hand-off report.** Give me the URL, tier, image tag, AWS account and region, the state
   bucket, what's left for me to do (DNS, alarm-email confirmation, n8n), and the day-2 commands
   from DEPLOY.md section 6 for updating, rolling back and changing tier.
