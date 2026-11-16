# Website free trial: "try the IT help desk"

Lets visitors use the help desk on the company website:

| Who | Price | What they get | Then |
|---|---|---|---|
| Visitor, no sign-up | free | 5 questions in total | must sign up |
| New account (verified email) | free | a one-time 10-question trial | buy a credit pack or a plan |
| Credit pack | $20 | 50 questions; never expire; used after any monthly allowance | |
| Starter (`starter`) | $79/month | 250 questions a month | buy credits or move up |
| Pro (`pro`) | $149/month | 600 questions a month | buy credits |

The question counts are settings on the agent (`WEBCHAT_VISITOR_FREE_USES`,
`WEBCHAT_SIGNUP_FREE_QUESTIONS`, `WEBCHAT_PLAN_MONTHLY_LIMITS`), not code. Prices live on the
website and in the payment provider; the agent only knows plan names and question counts. The
monthly allowances for Starter and Pro are placeholders until the owner confirms them.

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
| `netlify/lib/helpdesk-stripe.mjs` | `netlify/lib/` | Stripe Checkout, webhook and customer portal. No dependencies (no Stripe SDK) |
| `netlify/functions/api-helpdesk-billing.mjs` | `netlify/functions/` | Wires the Stripe logic to the site's helpers |
| `test/*.test.mjs` | (stays here) | `node --test integrations/website/test/*.test.mjs` (25 tests) |

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
4. Add the page that `WEBCHAT_UPGRADE_URL` points to (plans and credit packs), then follow
   *Payments* below.

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
  "used": 1, "allowance": 5, "remaining": 4, "credits": 0,   // remaining = allowance left + credits
  "signup_url": null, "upgrade_url": null
}
```

- `signup_required`: show the sign-up form. `reply` already explains why.
- `limit_reached`: link to `upgrade_url`. `reply` offers a credit pack or a monthly plan (it words
  the end of the free trial differently from a used-up monthly allowance).
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

## Payments (Stripe Checkout)

```
browser ─► POST /api/helpdesk/checkout {"product": "credits" | "starter" | "pro"} ─► {"url"} ─► Stripe's page
Stripe  ─► POST /api/helpdesk/stripe-webhook (signed by Stripe) ─► agent  POST /v1/webchat/grants
browser ─► POST /api/helpdesk/portal ─► {"url"} ─► Stripe's customer portal (cancel, switch plan, update card)
```

Card details never touch the site. What someone bought is decided only by Stripe's signed
webhook, never by the browser returning from checkout. Only a signed-in (verified) account can
buy, and the purchase is tied to that address on the server.

| Stripe event | What happens |
|---|---|
| `checkout.session.completed`, `checkout.session.async_payment_succeeded` (paid) | Credit pack: +50 questions. Subscription: plan set to `starter` or `pro` |
| `customer.subscription.updated` | Plan follows the subscription as it is now (upgrade, downgrade). A failed renewal keeps the plan while Stripe retries the card |
| `customer.subscription.deleted` | Plan back to `free`. Unused credits are kept |
| `charge.refunded`, `charge.dispute.created` | The owner is alerted. Credits already granted are not removed automatically |

Each grant is keyed by Stripe's own ID (the checkout session, or the event), and the agent
applies each key once, so Stripe's retries can never grant twice. If the agent can't be
reached, the webhook answers 500 and Stripe retries for up to three days.

### One-time Stripe setup (do it in test mode first)

1. **Products and prices.** Create three: *Credit pack*, $20, one-time. *Starter*, $79, recurring
   monthly. *Pro*, $149, recurring monthly. Copy each price ID (`price_...`).
2. **API key.** Developers → API keys → create a *restricted* key with write access to Checkout
   Sessions and Customer portal, and read access to Customers and Subscriptions. Nothing else.
3. **Webhook.** Developers → Webhooks → add endpoint
   `https://valleyagentworks.com/api/helpdesk/stripe-webhook` with exactly the six events in
   the table above. Copy its signing secret (`whsec_...`).
4. **Customer portal.** Settings → Billing → Customer portal: allow cancelling, and switching
   between Starter and Pro. Save (the portal link doesn't work until this is saved once).
5. **Netlify environment variables:** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
   `STRIPE_PRICE_CREDITS`, `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PRO` (and optionally
   `HELPDESK_CREDIT_PACK_SIZE`, default 50).
6. **Try it in test mode** with card `4242 4242 4242 4242`: buy a pack (the counter goes up by
   50), subscribe, switch plan and cancel in the portal. Then repeat steps 1 to 5 with live keys.

Test mode and live mode have separate keys, price IDs and webhook secrets.

### Buttons on the plans page

```js
async function buy(product) {                      // "credits" | "starter" | "pro"
  const r = await fetch("/api/helpdesk/checkout", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ product }) });
  if (r.status === 401) { location.href = "/helpdesk/?signup=needed"; return; }   // sign up first
  const d = await r.json();
  if (d.url) location.href = d.url;                // Stripe Checkout, or the portal if already subscribed
}
```

After paying, Stripe sends the visitor to `/helpdesk/?checkout=done`. The questions usually
arrive within a few seconds; if the counter hasn't moved yet, say "your purchase is being
confirmed" rather than treating the return itself as proof of payment.

### Not handled automatically

- **Refunds and disputes** alert the owner only. Removing questions is a manual decision.
- **Sales tax.** Whether to turn on Stripe Tax is the owner's decision; nothing here depends on it.
- **Allowances reset on the 1st of each calendar month (UTC)**, not on the billing date.

## Security rules for whoever edits the website side

- Keep `HELPDESK_SIGNING_SECRET` and `HELPDESK_COOKIE_SECRET` in Netlify environment variables
  only. Never in the repo, the page, or a `NEXT_PUBLIC_`-style variable.
- Take the visitor's address from Netlify (`context.ip`), never from a request header.
- Render `reply` as text. It is model output grounded in SOPs, and must never be treated as HTML.
- Don't add a way for the browser to send `account_email`, `visitor_id` or `client_ip`.
- Don't call `/v1/webchat/grants` from anything a browser can reach. Only the Stripe webhook does.
- The webhook must read the raw request body (`req.text()`): Stripe signs the exact bytes.
- Never send a price ID, an amount or an email from the browser to Stripe. The server picks all three.
- The sign-up form answers `{"ok": true}` whether or not the address exists. Keep it that way.

## What the visitor is told

Answers cite the SOP they came from. Requests outside IT support get the same courteous decline
as in production. Anything a live deployment would hand to a technician (low confidence, no
documented fix, admin work, security reports, software requests) says so plainly, and says that
this demo isn't connected to a technician, so nobody will follow up. It never promises a callback.
