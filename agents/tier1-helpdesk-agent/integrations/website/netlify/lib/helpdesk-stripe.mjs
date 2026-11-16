// Payments for the help desk free trial, with Stripe Checkout. No imports besides
// node:crypto and the sibling core file, so it is unit-tested as is.
//
//   browser ─► POST /api/helpdesk/checkout {product} ─► Stripe Checkout page (hosted by Stripe)
//   Stripe  ─► POST /api/helpdesk/stripe-webhook (signed) ─► agent  POST /v1/webchat/grants
//   browser ─► POST /api/helpdesk/portal ─► Stripe customer portal (cancel, change plan, cards)
//
// Card details never touch this site. What a person bought is decided only by Stripe's
// signed webhook, never by the browser coming back from the checkout page.
//
// Products (create each in Stripe, put its price ID in the environment):
//   credits   one-time     STRIPE_PRICE_CREDITS   adds HELPDESK_CREDIT_PACK_SIZE questions (default 50)
//   starter   monthly      STRIPE_PRICE_STARTER   sets plan "starter"
//   pro       monthly      STRIPE_PRICE_PRO       sets plan "pro"
import crypto from "node:crypto";
import { createAgentClient, accountFromCookies } from "./helpdesk-core.mjs";

export const BILLING_PATHS = ["/api/helpdesk/checkout", "/api/helpdesk/portal", "/api/helpdesk/stripe-webhook"];
const STRIPE = "https://api.stripe.com/v1";
const WEBHOOK_TOLERANCE_S = 300;
// Subscription states that keep the plan. past_due stays on while Stripe retries the card.
const ACTIVE = new Set(["active", "trialing", "past_due"]);

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
}

function safeEqual(a, b) {
  const x = Buffer.from(String(a)), y = Buffer.from(String(b));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}

// Stripe-Signature: t=<unix>,v1=<hex>[,v1=<hex>...]; v1 = HMAC-SHA256(secret, "<t>." + raw body).
export function verifyStripeSignature(secret, rawBody, header, now = Date.now(), toleranceSeconds = WEBHOOK_TOLERANCE_S) {
  if (!secret || !header) return false;
  let timestamp = null;
  const candidates = [];
  for (const part of String(header).split(",")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    const key = part.slice(0, i).trim(), value = part.slice(i + 1).trim();
    if (key === "t") timestamp = value;
    else if (key === "v1") candidates.push(value);
  }
  if (!timestamp || !/^\d{1,12}$/.test(timestamp) || !candidates.length) return false;
  if (Math.abs(now / 1000 - Number(timestamp)) > toleranceSeconds) return false;
  const expected = crypto.createHmac("sha256", secret).update(`${timestamp}.${rawBody}`).digest("hex");
  return candidates.some((c) => safeEqual(c, expected));
}

// {a: {b: [1, {c: 2}]}} -> a[b][0]=1&a[b][1][c]=2 (Stripe's form encoding)
export function formEncode(obj, prefix = "", out = new URLSearchParams()) {
  for (const [key, value] of Object.entries(obj)) {
    if (value === undefined || value === null) continue;
    const name = prefix ? `${prefix}[${key}]` : key;
    if (typeof value === "object") formEncode(value, name, out);
    else out.append(name, String(value));
  }
  return out;
}

/**
 * deps:
 *   env        HELPDESK_* (see helpdesk-core.mjs) plus STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
 *              STRIPE_PRICE_CREDITS, STRIPE_PRICE_STARTER, STRIPE_PRICE_PRO, HELPDESK_CREDIT_PACK_SIZE
 *   fetch      fetch implementation
 *   originOk   (req) => boolean
 *   rateLimit  async (req, bucket, max, windowSeconds) => boolean
 *   alert      optional async (subject, detail) => void   (tell the owner about refunds and disputes)
 */
export function createBillingHandler(deps) {
  const env = deps.env || {};
  const siteUrl = String(env.HELPDESK_SITE_URL || "").replace(/\/+$/, "");
  const cookieSecret = env.HELPDESK_COOKIE_SECRET || "";
  const stripeKey = env.STRIPE_SECRET_KEY || "";
  const webhookSecret = env.STRIPE_WEBHOOK_SECRET || "";
  const packSize = Math.max(1, Math.min(100000, parseInt(env.HELPDESK_CREDIT_PACK_SIZE || "50", 10) || 50));
  const products = {
    credits: { price: env.STRIPE_PRICE_CREDITS || "", mode: "payment" },
    starter: { price: env.STRIPE_PRICE_STARTER || "", mode: "subscription" },
    pro: { price: env.STRIPE_PRICE_PRO || "", mode: "subscription" },
  };
  const planOfPrice = (priceId) =>
    Object.keys(products).find((p) => products[p].mode === "subscription" && products[p].price && products[p].price === priceId) || null;
  const configured = siteUrl.startsWith("https://") && cookieSecret.length >= 32 && stripeKey.length >= 20
    && webhookSecret.length >= 20 && Object.values(products).every((p) => p.price);
  const { grant } = createAgentClient({ env, fetch: deps.fetch });

  async function stripe(method, path, params, idempotencyKey) {
    const headers = { Authorization: `Bearer ${stripeKey}` };
    let url = `${STRIPE}${path}`;
    let body;
    if (method === "GET") {
      if (params) url += `?${formEncode(params)}`;
    } else {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
      body = formEncode(params || {}).toString();
    }
    const res = await deps.fetch(url, { method, headers, body, signal: AbortSignal.timeout(20000) });
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) {
      const message = (data && data.error && data.error.message) || `HTTP ${res.status}`;
      throw new Error(`Stripe ${method} ${path} failed: ${message}`);
    }
    return data;
  }

  async function customerFor(email) {
    const found = await stripe("GET", "/customers", { email, limit: 1 });
    return found && found.data && found.data[0] ? found.data[0] : null;
  }

  async function checkout(req) {
    if (!(await deps.rateLimit(req, "helpdesk-checkout", 20, 3600))) return json({ error: "rate_limited" }, 429);
    // Only a signed-in (verified) account can buy: the purchase is tied to that address.
    const email = accountFromCookies(cookieSecret, req.headers.get("cookie"));
    if (!email) return json({ error: "sign_in_required" }, 401);
    let body;
    try { body = await req.json(); } catch { return json({ error: "bad_request" }, 400); }
    const product = typeof body?.product === "string" ? body.product : "";
    if (!Object.hasOwn(products, product)) return json({ error: "unknown_product" }, 422);
    const { price, mode } = products[product];

    const customer = await customerFor(email);
    if (mode === "subscription" && customer) {
      const subs = await stripe("GET", "/subscriptions", { customer: customer.id, status: "all", limit: 10 });
      if ((subs.data || []).some((s) => ACTIVE.has(s.status))) {
        // Already subscribed: change plan in the portal instead of paying for two subscriptions.
        const portal = await stripe("POST", "/billing_portal/sessions",
          { customer: customer.id, return_url: `${siteUrl}/helpdesk/plans.html` });
        return json({ url: portal.url, already_subscribed: true });
      }
    }
    const metadata = { email, product };
    const session = await stripe("POST", "/checkout/sessions", {
      mode,
      line_items: [{ price, quantity: 1 }],
      ...(customer ? { customer: customer.id } : { customer_email: email }),
      client_reference_id: crypto.createHash("sha256").update(email).digest("hex").slice(0, 40),
      metadata,
      ...(mode === "subscription" ? { subscription_data: { metadata } } : { payment_intent_data: { metadata } }),
      success_url: `${siteUrl}/helpdesk/?checkout=done`,
      cancel_url: `${siteUrl}/helpdesk/plans.html?checkout=cancelled`,
    });
    return json({ url: session.url });
  }

  async function portal(req) {
    if (!(await deps.rateLimit(req, "helpdesk-portal", 20, 3600))) return json({ error: "rate_limited" }, 429);
    const email = accountFromCookies(cookieSecret, req.headers.get("cookie"));
    if (!email) return json({ error: "sign_in_required" }, 401);
    const customer = await customerFor(email);
    if (!customer) return json({ error: "no_purchases" }, 404);
    const session = await stripe("POST", "/billing_portal/sessions",
      { customer: customer.id, return_url: `${siteUrl}/helpdesk/plans.html` });
    return json({ url: session.url });
  }

  async function applyGrant(payload) {
    const { status, data } = await grant(payload);
    if (status !== 200) throw new Error(`agent refused grant ${payload.grant_id}: HTTP ${status}`);
    return data;
  }

  async function emailOfSubscription(sub) {
    const fromMetadata = String((sub.metadata && sub.metadata.email) || "").toLowerCase();
    if (fromMetadata) return fromMetadata;
    const customerId = typeof sub.customer === "string" ? sub.customer : sub.customer && sub.customer.id;
    if (!customerId) return "";
    const customer = await stripe("GET", `/customers/${encodeURIComponent(customerId)}`);
    return String((customer && customer.email) || "").toLowerCase();
  }

  async function onEvent(event) {
    const object = (event.data && event.data.object) || {};
    switch (event.type) {
      case "checkout.session.completed":
      case "checkout.session.async_payment_succeeded": {
        // "completed" can arrive before a slow payment method has paid; wait for the money.
        if (object.payment_status !== "paid") return { ignored: "not paid yet" };
        const email = String((object.metadata && object.metadata.email) || "").toLowerCase();
        const product = object.metadata && object.metadata.product;
        if (!email || !Object.hasOwn(products, product || "")) return { ignored: "not a help desk checkout" };
        if (products[product].mode !== object.mode) return { ignored: "mode mismatch" };
        // The session ID is the grant ID: both events for one session, and any retry, grant once.
        if (product === "credits") return applyGrant({ grant_id: object.id, email, add_credits: packSize });
        return applyGrant({ grant_id: object.id, email, plan: product });
      }
      case "customer.subscription.updated":
      case "customer.subscription.deleted": {
        // Events can arrive out of order, so act on the subscription as it is NOW.
        let current = object;
        try {
          current = await stripe("GET", `/subscriptions/${encodeURIComponent(object.id)}`);
        } catch (e) {
          if (event.type !== "customer.subscription.deleted") throw e;
        }
        const email = await emailOfSubscription(current);
        if (!email) return { ignored: "no email on subscription" };
        const priceId = current.items && current.items.data && current.items.data[0] && current.items.data[0].price
          && current.items.data[0].price.id;
        const plan = ACTIVE.has(current.status) ? planOfPrice(priceId) : "free";
        if (!plan) return { ignored: "not a help desk plan" };
        return applyGrant({ grant_id: event.id, email, plan });
      }
      case "charge.refunded":
      case "charge.dispute.created":
        // Money came back out. Credits already granted are not removed automatically: decide by hand.
        if (deps.alert) await deps.alert(`Help desk payment: ${event.type}`, `Stripe object ${object.id}`);
        return { alerted: true };
      default:
        return { ignored: event.type };
    }
  }

  async function webhook(req) {
    const raw = await req.text(); // the exact bytes Stripe signed
    if (!verifyStripeSignature(webhookSecret, raw, req.headers.get("stripe-signature"))) {
      return json({ error: "bad_signature" }, 400);
    }
    let event;
    try { event = JSON.parse(raw); } catch { return json({ error: "bad_request" }, 400); }
    if (!event || typeof event.id !== "string" || typeof event.type !== "string") return json({ error: "bad_request" }, 400);
    try {
      const result = await onEvent(event);
      return json({ received: true, ...(result && result.ignored ? { ignored: result.ignored } : {}) });
    } catch (e) {
      // A 5xx makes Stripe retry (for up to 3 days). Grants are applied once per ID, so retries are safe.
      console.error("helpdesk stripe webhook failed", event.type, e && e.message);
      return json({ error: "retry" }, 500);
    }
  }

  const handler = async (req) => {
    const url = new URL(req.url);
    if (!configured) return json({ error: "not_configured" }, 503);
    if (req.method !== "POST") return json({ error: "method" }, 405);
    try {
      if (url.pathname === "/api/helpdesk/stripe-webhook") return await webhook(req); // Stripe's signature is the auth
      if (!deps.originOk(req)) return json({ error: "forbidden" }, 403);
      if (url.pathname === "/api/helpdesk/checkout") return await checkout(req);
      if (url.pathname === "/api/helpdesk/portal") return await portal(req);
      return json({ error: "not_found" }, 404);
    } catch (e) {
      console.error("helpdesk billing error", e && e.message);
      return json({ error: "server" }, 500);
    }
  };
  handler.onEvent = onEvent;
  return handler;
}
