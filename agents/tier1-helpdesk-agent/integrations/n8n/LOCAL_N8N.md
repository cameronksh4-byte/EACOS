# Using a local n8n (on your own machine)

Tier 0 doesn't need n8n. For Tier 1 and 2 you can run the workflows on an n8n instance on your
own computer or server. Only one thing needs care: the agent runs in AWS and Slack runs in the
cloud, and both must reach your n8n.

```
agent (AWS) ──approval cards, alerts──►┐
Slack ───────button clicks────────────►├─► https://<public URL>/webhook/*  ──tunnel──► local n8n :5678
                                       ┘
local n8n ──signed callbacks, reports──► https://<agent hostname>   (outbound only: works already)
```

So n8n's **`/webhook/` paths** need a public HTTPS URL. The n8n **editor and API should
stay private.** A tunnel that forwards only `/webhook/*` gives you exactly that.

## 1. Check your n8n version

The workflows use Webhook v2.2, Respond to Webhook v1.5, Code v2 and Schedule Trigger v1.4, and
were tested end to end on **n8n 2.47.5**. Check *Settings → About n8n* (or `n8n --version`). On an
older version, upgrade first, or the import fails with an unknown node version. n8n 2.x on npm
needs Node.js 24 or newer; the Docker image (`n8nio/n8n:2.47.5`) includes it.

## 2. Expose only the webhooks (Cloudflare Tunnel, free)

You need a domain on Cloudflare (any plan). Then:

1. Cloudflare dashboard → *Zero Trust → Networks → Tunnels → Create a tunnel* (type *Cloudflared*),
   named for example `helpdesk-n8n`.
2. Install the connector on the machine running n8n, using the command the dashboard shows
   (`cloudflared service install <token>`, or as a container:
   `docker run -d --restart unless-stopped cloudflare/cloudflared:latest tunnel --no-autoupdate run --token <token>`).
3. Add one **public hostname** route:
   - Subdomain/domain: e.g. `n8n-hooks.acme.com`
   - **Path: `^/webhook/`**
   - Service: `http://localhost:5678` (from a container: `http://host.docker.internal:5678`)

   Don't add any other route. Requests outside `/webhook/` then get a 404 from Cloudflare and
   never reach n8n, so the editor, `/rest` and `/api` stay private.
4. Check it: `curl -i https://n8n-hooks.acme.com/` should return **404**. A webhook path should
   reach n8n (which answers 404 until the workflows are published).

Alternatives work too, as long as they expose only `/webhook/*` over HTTPS with a **stable**
hostname: ngrok with a static domain plus a traffic policy that rejects other paths, or Tailscale
Funnel. Quick-tunnel URLs that change on every restart will break the setup the next time they
change.

## 3. Configure

In `deploy/<client>.env` set:

```bash
N8N_PUBLIC_URL=https://n8n-hooks.acme.com        # the tunnel; webhook URLs are derived from it
N8N_API_URL=http://localhost:5678                 # deploy.sh imports the workflows from your machine
N8N_API_KEY=…                                     # n8n: Settings → n8n API → Create an API key
N8N_SLACK_ALERT_CHANNEL=C…                        # optional; defaults to SLACK_ESCALATION_CHANNEL
N8N_SLACK_SOP_REVIEW_CHANNEL=C…                   # Tier 2: where the IT manager reviews drafts
N8N_SLACK_EXEC_CHANNEL=C…                         # Tier 2: weekly executive report
N8N_PROVISIONING_PROVIDER=entra                   # entra | okta | none
```

**Keep `SLACK_ESCALATION_CHANNEL` set as well.** If n8n is unreachable, the agent falls back to
posting alerts and escalations there directly.

## 4. Load n8n's environment

Every `deploy/deploy.sh` run writes `deploy/<client>.n8n.env` (mode 600, git-ignored), with the
shared secrets copied from your deploy file, so the two never drift. You can also generate it
by itself:

```bash
python3 deploy/render_config.py deploy/acme-corp.env --n8n-env deploy/acme-corp.n8n.env
```

Load it into n8n and **restart n8n** (n8n reads environment variables only at start):

- **Docker:** add `--env-file /path/to/acme-corp.n8n.env` to your `docker run`, or
  `env_file: [/path/to/acme-corp.n8n.env]` in docker compose, then recreate the container
  (`docker compose up -d`; a plain `restart` doesn't re-read env files).
- **npm:** `set -a; . /path/to/acme-corp.n8n.env; set +a; n8n start`

The file includes `WEBHOOK_URL=<N8N_PUBLIC_URL>/`, so n8n shows the public webhook URLs.

## 5. Import, connect Slack, verify

1. `deploy/deploy.sh deploy/acme-corp.env` imports and publishes the four workflows through
   `N8N_API_URL`. Without an API key: *Workflows → Import from File* for each
   `integrations/n8n/workflows/*.json`, then **Publish** each one.
2. In your Slack app: *Interactivity & Shortcuts → Request URL* =
   `https://n8n-hooks.acme.com/webhook/helpdesk-slack-actions`.
3. Verify. The deploy runs this automatically, and you can run it any time:
   ```bash
   python3 deploy/n8n_ping.py deploy/acme-corp.env
   ```
   It sends a signed no-op event to both webhooks through the public URL:

   | Result | Meaning |
   |---|---|
   | `PASS` | Tunnel up, workflow published, secrets match |
   | `HTTP 401` | n8n's `N8N_AGENT_SIGNING_SECRET` ≠ deploy file. Reload the env file and restart n8n |
   | `HTTP 404` | Workflows not published (or wrong path) |
   | `not reachable` | n8n or the tunnel is down |

## 6. When your machine is off

n8n only works while the machine and the tunnel are up. Laptops that sleep are the usual
problem: use an always-on machine or small VM, or stop it sleeping (macOS: *Battery/Energy →
prevent automatic sleeping*; Windows: power plan *never sleep* on AC). The agent is built to
cope when n8n is down:

| While n8n is unreachable | What happens |
|---|---|
| Someone requests software that needs approval | The card can't be delivered, so the request is cancelled and the ticket goes to Tier 2. The user is told a technician will follow up |
| An approver clicks a button | Slack shows an error. Clicking again later works while the approval is valid (72 h); after that the thread escalates |
| Outage or escalation alert | Posted straight to `SLACK_ESCALATION_CHANNEL` instead. Without that channel, retried every minute until n8n is back |
| New SOP draft (Tier 2) | Saved. The review notice is retried every minute until n8n accepts it |
| Monday weekly report | Runs once when n8n is next up (missed-run policy on the schedule) |

Ticket triage itself never depends on n8n.

## 7. Security notes

- Only `/webhook/*` is public, and every webhook checks a signature (the agent's HMAC, or
  Slack's) before doing anything. Unsigned or replayed requests get 401.
- `deploy/<client>.n8n.env` holds secrets: keep it mode 600 and never commit it.
- The workflows read their settings from n8n environment variables, so anyone who can edit
  workflows on this n8n could read them. Keep the editor private (the tunnel does) and the
  n8n owner account to yourself.
