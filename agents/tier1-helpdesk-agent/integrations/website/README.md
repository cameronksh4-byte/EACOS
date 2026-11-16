# Website free trial: "try the IT help desk"

Lets visitors use the help desk on the company website:

| Who | What they get | Then |
|---|---|---|
| Visitor, no sign-up | 5 questions in total | must sign up |
| Free account (verified email) | 20 questions a month | upgrade or buy credits |
| Starter, $29/month | 250 questions a month | buy credits or move up a tier |
| Credit pack | extra questions, used after the monthly allowance, never expire | |

Every number above is a setting on the agent (`WEBCHAT_VISITOR_FREE_USES`,
`WEBCHAT_PLAN_MONTHLY_LIMITS`), not code. Prices live on the website and in the payment
provider; the agent only knows plan names and question counts.

## How it fits together

```
browser ──► website server (Netlify function) ──signed──► agent  POST /v1/webchat/message
            sets the visitor cookie                        counts the question in the database,
            runs sign-up (magic link)                      answers from the web-demo SOPs
            holds the secrets                              (no side effects: nobody is paged)
payment provider ──► website server ──signed──► agent     POST /v1/webchat/grants
```

The browser never calls the agent and never holds a secret. All counting happens on the agent,
in one database transaction, before any model call. The website can't grant extra questions by
mistake, and a visitor can't by tampering.

## What's in this folder

| File | Where it goes on the website | What it is |
|---|---|---|
| `netlify/lib/helpdesk-core.mjs` | `netlify/lib/` | All the logic. No dependencies. Don't edit; it's tested here |
| `netlify/functions/api-helpdesk.mjs` | `netlify/functions/` | Wires the logic to the site's existing helpers (`originOk`, `rateLimit`, `sendEmail`, Blobs) |
| `assets/helpdesk-widget.js` | `assets/helpdesk/` | A plain, unstyled chat widget to restyle or replace |
| `test/helpdesk-core.test.mjs` | (stays here) | `node --test integrations/website/test/helpdesk-core.test.mjs` |

## Website setup

1. Copy the two `netlify/` files and the widget into the site.
2. Set four environment variables in Netlify (names only; values are secrets):

   | Variable | Value |
   |---|---|
   | `HELPDESK_AGENT_URL` | `https://<the agent's public hostname>` |
   | `HELPDESK_SIGNING_SECRET` | exactly the agent's `WEBCHAT_SIGNING_SECRET` |
   | `HELPDESK_COOKIE_SECRET` | a different long random string |
   | `HELPDESK_SITE_URL` | `https://valleyagentworks.com` |

3. Build a page at `/helpdesk/` with `<div id="helpdesk"></div>` and the widget script. After
   the emailed link is opened, the visitor lands on `/helpdesk/?signup=done` (or
   `?signup=expired`).
4. Add the page that `WEBCHAT_UPGRADE_URL` points to (plans and credit packs).

## Agent setup (deploy env file, section 6)

```
WEBCHAT_ENABLED=true
WEBCHAT_SIGNING_SECRET=<deploy/render_config.py --generate-secrets>
WEBCHAT_SOP_DIR=ingest/sample_sops
WEBCHAT_SIGNUP_URL=https://valleyagentworks.com/helpdesk/
WEBCHAT_UPGRADE_URL=https://valleyagentworks.com/helpdesk/plans.html
```

Use the company's own deployment for this, not a client's. The chat has its own tenant
(`web-demo`), its own SOPs and its own hard monthly model budget (`WEBCHAT_MONTHLY_BUDGET_USD`,
default $10). While it's off, the three routes return 404.

## Browser-facing API (served by the website)

| Route | Body | Returns |
|---|---|---|
| `POST /api/helpdesk/ask` | `{"message": "..."}` (2 to 1000 characters) | the answer object below |
| `POST /api/helpdesk/signup` | `{"email", "name"?, "business"?, "lang"?}` | `{"ok": true}` always; emails a 20-minute link |
| `GET /api/helpdesk/verify?t=...` | (the emailed link) | sets the sign-in cookie, redirects to `/helpdesk/` |
| `GET /api/helpdesk/me` | | `{"signed_in", "plan", "used", "allowance", "credits"}` |
| `POST /api/helpdesk/signout` | | `{"ok": true}` |

Answer object:

```jsonc
{
  "status": "answered",            // answered | signup_required | limit_reached | unavailable
  "outcome": "resolved",           // resolved | escalated | declined (only when answered)
  "reply": "1. Disconnect and reconnect…",   // PLAIN TEXT. Insert with textContent, never innerHTML
  "cited_sop_ids": ["SOP-NET-001"],
  "signed_in": false, "plan": null,
  "used": 1, "allowance": 5, "remaining": 4, "credits": 0,
  "signup_url": null, "upgrade_url": null
}
```

- `signup_required`: show the sign-up form. `reply` already explains why.
- `limit_reached`: link to `upgrade_url`. `reply` offers credits or the next plan.
- `unavailable`: the question was not counted. Let them try again.

## Agent API (called only by the website's server)

Every request carries `X-Agent-Signature: t=<unix seconds>,v1=<hex>`, where
`v1 = HMAC-SHA256(WEBCHAT_SIGNING_SECRET, "<t>." + raw body)`. Requests older than 5 minutes are
refused. `helpdesk-core.mjs` does this for you (`signAgent`).

| Route | Body | Notes |
|---|---|---|
| `POST /v1/webchat/message` | `{"visitor_id", "client_ip", "account_email", "message"}` | `account_email` only once the address is verified, else `null` |
| `POST /v1/webchat/grants` | `{"grant_id", "email", "plan"?, "add_credits"?}` | Plan change and/or credits. Applied once per `grant_id` |
| `POST /v1/webchat/account` | `{"email"}` | Plan, usage this month, credits. 404 if the account has never asked a question or received a grant |

## Payments (not built yet: the provider is still to be chosen)

Whatever takes the money, the hand-over to the agent is one signed call from the website's
server once payment is confirmed:

```js
import handler from "./api-helpdesk.mjs";
await handler.grant({ grant_id: "<the payment's own ID>", email, plan: "starter" });       // subscription started
await handler.grant({ grant_id: "<the payment's own ID>", email, add_credits: 100 });      // credit pack bought
await handler.grant({ grant_id: "<cancellation event ID>", email, plan: "free" });         // subscription ended
```

Use the payment's own ID as `grant_id`. Payment providers retry their webhooks; the agent applies
each `grant_id` once, so a retry never grants twice. Verify the provider's webhook signature
before calling `grant`, and take the email from the payment record, never from the browser.

## Security rules for whoever edits the website side

- Keep `HELPDESK_SIGNING_SECRET` and `HELPDESK_COOKIE_SECRET` in Netlify environment variables
  only. Never in the repo, the page, or a `NEXT_PUBLIC_`-style variable.
- Take the visitor's address from Netlify (`context.ip`), never from a request header.
- Render `reply` as text. It is model output grounded in SOPs, and must never be treated as HTML.
- Don't add a way for the browser to send `account_email`, `visitor_id` or `client_ip`.
- Don't call `/v1/webchat/grants` from anything a browser can reach.
- The sign-up form answers `{"ok": true}` whether or not the address exists. Keep it that way.

## What the visitor is told

Answers cite the SOP they came from. Requests outside IT support get the same courteous decline
as in production. Anything a live deployment would hand to a technician (low confidence, no
documented fix, admin work, security reports, software requests) says so plainly, and says that
this demo isn't connected to a technician, so nobody will follow up. It never promises a callback.
