// node --test integrations/website/test/helpdesk-stripe.test.mjs
import test from "node:test";
import assert from "node:assert";
import crypto from "node:crypto";
import { makeToken, ACCOUNT_COOKIE } from "../netlify/lib/helpdesk-core.mjs";
import { createBillingHandler, verifyStripeSignature, formEncode } from "../netlify/lib/helpdesk-stripe.mjs";

const COOKIE = "c".repeat(40), WHSEC = "whsec_" + "w".repeat(32);
const env = {
  HELPDESK_AGENT_URL: "https://helpdesk.example.com", HELPDESK_SIGNING_SECRET: "s".repeat(40),
  HELPDESK_COOKIE_SECRET: COOKIE, HELPDESK_SITE_URL: "https://valleyagentworks.com",
  STRIPE_SECRET_KEY: "rk_test_" + "k".repeat(24), STRIPE_WEBHOOK_SECRET: WHSEC,
  STRIPE_PRICE_CREDITS: "price_credits", STRIPE_PRICE_STARTER: "price_starter", STRIPE_PRICE_PRO: "price_pro",
};

// Fake Stripe + fake agent behind one fetch.
function setup(o = {}) {
  const stripeCalls = [], grants = [], alerts = [];
  const state = { customers: o.customers || [], subscriptions: o.subscriptions || {}, agentStatus: o.agentStatus || 200 };
  const reply = (obj, status = 200) => new Response(JSON.stringify(obj), { status });
  const handler = createBillingHandler({
    env: o.env || env,
    originOk: (req) => req.headers.get("origin") === "https://valleyagentworks.com",
    rateLimit: async () => true,
    alert: async (subject, detail) => { alerts.push([subject, detail]); },
    fetch: async (url, init) => {
      const u = new URL(url);
      if (u.host === "helpdesk.example.com") {
        grants.push(JSON.parse(init.body));
        return reply({ applied: true, plan: "x", credits: 0 }, state.agentStatus);
      }
      assert.strictEqual(u.host, "api.stripe.com");
      assert.strictEqual(init.headers.Authorization, `Bearer ${env.STRIPE_SECRET_KEY}`);
      const call = { method: init.method, path: u.pathname, query: Object.fromEntries(u.searchParams),
        form: init.body ? Object.fromEntries(new URLSearchParams(init.body)) : null, headers: init.headers };
      stripeCalls.push(call);
      if (call.method === "GET" && u.pathname === "/v1/customers") {
        return reply({ data: state.customers.filter((c) => c.email === call.query.email) });
      }
      if (call.method === "GET" && u.pathname.startsWith("/v1/customers/")) {
        return reply(state.customers.find((c) => c.id === u.pathname.split("/").pop()) || {});
      }
      if (call.method === "GET" && u.pathname === "/v1/subscriptions") {
        return reply({ data: Object.values(state.subscriptions).filter((s) => s.customer === call.query.customer) });
      }
      if (call.method === "GET" && u.pathname.startsWith("/v1/subscriptions/")) {
        const sub = state.subscriptions[u.pathname.split("/").pop()];
        return sub ? reply(sub) : reply({ error: { message: "No such subscription" } }, 404);
      }
      if (u.pathname === "/v1/checkout/sessions") return reply({ id: "cs_test_1", url: "https://checkout.stripe.com/c/pay/cs_test_1" });
      if (u.pathname === "/v1/billing_portal/sessions") return reply({ url: "https://billing.stripe.com/p/session/x" });
      return reply({ error: { message: "unexpected" } }, 400);
    },
  });
  return { handler, stripeCalls, grants, alerts, state };
}

const signedIn = (email = "pat@example.com") => `${ACCOUNT_COOKIE}=${makeToken(COOKIE, "account", email, 3600)}`;
const post = (path, body, headers = {}) => new Request(`https://valleyagentworks.com${path}`, {
  method: "POST", body: JSON.stringify(body),
  headers: { "content-type": "application/json", origin: "https://valleyagentworks.com", ...headers } });
function stripeEvent(event, secret = WHSEC, at = Math.floor(Date.now() / 1000)) {
  const raw = JSON.stringify(event);
  const sig = crypto.createHmac("sha256", secret).update(`${at}.${raw}`).digest("hex");
  return new Request("https://valleyagentworks.com/api/helpdesk/stripe-webhook", {
    method: "POST", body: raw, headers: { "stripe-signature": `t=${at},v1=${sig}` } });
}
const session = (over = {}) => ({ id: "evt_1", type: "checkout.session.completed", data: { object: {
  id: "cs_live_abc123", mode: "payment", payment_status: "paid",
  metadata: { email: "pat@example.com", product: "credits" }, ...over } } });
const sub = (over = {}) => ({ id: "sub_1", customer: "cus_1", status: "active", metadata: { email: "pat@example.com" },
  items: { data: [{ price: { id: "price_starter" } }] }, ...over });

test("Stripe signature: valid, tampered, wrong secret, stale, rotated secrets", () => {
  const raw = '{"id":"evt_1"}', t = 1_700_000_000;
  const good = crypto.createHmac("sha256", WHSEC).update(`${t}.${raw}`).digest("hex");
  const now = t * 1000;
  assert.ok(verifyStripeSignature(WHSEC, raw, `t=${t},v1=${good}`, now));
  assert.ok(verifyStripeSignature(WHSEC, raw, `t=${t},v1=${"0".repeat(64)},v1=${good},v0=x`, now), "any v1 may match");
  assert.ok(!verifyStripeSignature(WHSEC, raw + " ", `t=${t},v1=${good}`, now));
  assert.ok(!verifyStripeSignature("whsec_other", raw, `t=${t},v1=${good}`, now));
  assert.ok(!verifyStripeSignature(WHSEC, raw, `t=${t},v1=${good}`, now + 301_000), "older than 5 minutes");
  assert.ok(!verifyStripeSignature(WHSEC, raw, `v1=${good}`, now) && !verifyStripeSignature(WHSEC, raw, "", now));
  assert.strictEqual(formEncode({ a: { b: [1, { c: "x y" }] }, skip: null }).toString(), "a%5Bb%5D%5B0%5D=1&a%5Bb%5D%5B1%5D%5Bc%5D=x+y");
});

test("checkout: only a signed-in account, only from this site, only known products", async () => {
  const { handler, stripeCalls } = setup();
  assert.strictEqual((await handler(post("/api/helpdesk/checkout", { product: "credits" }))).status, 401);
  assert.strictEqual((await handler(post("/api/helpdesk/checkout", { product: "credits" }, { cookie: signedIn(), origin: "https://evil.example" }))).status, 403);
  assert.strictEqual((await handler(post("/api/helpdesk/checkout", { product: "enterprise" }, { cookie: signedIn() }))).status, 422);
  assert.strictEqual((await handler(post("/api/helpdesk/checkout", { product: "__proto__" }, { cookie: signedIn() }))).status, 422);
  assert.strictEqual(stripeCalls.length, 0);
});

test("checkout: credit pack is a one-time payment tied to the verified address", async () => {
  const { handler, stripeCalls } = setup();
  const res = await handler(post("/api/helpdesk/checkout", { product: "credits", email: "boss@example.com", price: "price_free" },
    { cookie: signedIn() }));
  assert.deepStrictEqual(await res.json(), { url: "https://checkout.stripe.com/c/pay/cs_test_1" });
  const create = stripeCalls.find((c) => c.path === "/v1/checkout/sessions").form;
  assert.strictEqual(create.mode, "payment");
  assert.strictEqual(create["line_items[0][price]"], "price_credits"); // the server's price, not the browser's
  assert.strictEqual(create["line_items[0][quantity]"], "1");
  assert.strictEqual(create.customer_email, "pat@example.com"); // from the sign-in cookie, not the request body
  assert.strictEqual(create["metadata[email]"], "pat@example.com");
  assert.strictEqual(create["metadata[product]"], "credits");
  assert.strictEqual(create.success_url, "https://valleyagentworks.com/helpdesk/?checkout=done");
  assert.ok(!JSON.stringify(create).includes("boss@example.com") && !JSON.stringify(create).includes("price_free"));
});

test("checkout: subscriptions reuse the Stripe customer and carry the address on the subscription", async () => {
  const { handler, stripeCalls } = setup({ customers: [{ id: "cus_1", email: "pat@example.com" }] });
  await handler(post("/api/helpdesk/checkout", { product: "pro" }, { cookie: signedIn() }));
  const create = stripeCalls.find((c) => c.path === "/v1/checkout/sessions").form;
  assert.strictEqual(create.mode, "subscription");
  assert.strictEqual(create["line_items[0][price]"], "price_pro");
  assert.strictEqual(create.customer, "cus_1");
  assert.ok(!("customer_email" in create));
  assert.strictEqual(create["subscription_data[metadata][email]"], "pat@example.com");
});

test("checkout: someone already subscribed is sent to the portal, not charged for a second subscription", async () => {
  const { handler, stripeCalls } = setup({ customers: [{ id: "cus_1", email: "pat@example.com" }], subscriptions: { sub_1: sub() } });
  const out = await (await handler(post("/api/helpdesk/checkout", { product: "pro" }, { cookie: signedIn() }))).json();
  assert.deepStrictEqual(out, { url: "https://billing.stripe.com/p/session/x", already_subscribed: true });
  assert.ok(!stripeCalls.some((c) => c.path === "/v1/checkout/sessions"));
  // A credit pack is still allowed on top of a subscription.
  const pack = await (await handler(post("/api/helpdesk/checkout", { product: "credits" }, { cookie: signedIn() }))).json();
  assert.strictEqual(pack.url, "https://checkout.stripe.com/c/pay/cs_test_1");
});

test("portal: signed-in customers get a manage/cancel link; others don't", async () => {
  const { handler } = setup({ customers: [{ id: "cus_1", email: "pat@example.com" }] });
  assert.strictEqual((await handler(post("/api/helpdesk/portal", {}))).status, 401);
  assert.strictEqual((await handler(post("/api/helpdesk/portal", {}, { cookie: signedIn("new@example.com") }))).status, 404);
  const out = await (await handler(post("/api/helpdesk/portal", {}, { cookie: signedIn() }))).json();
  assert.strictEqual(out.url, "https://billing.stripe.com/p/session/x");
});

test("webhook: unsigned, forged and stale events change nothing", async () => {
  const { handler, grants } = setup();
  const bare = new Request("https://valleyagentworks.com/api/helpdesk/stripe-webhook", { method: "POST", body: JSON.stringify(session()) });
  assert.strictEqual((await handler(bare)).status, 400);
  assert.strictEqual((await handler(stripeEvent(session(), "whsec_wrong_secret_wrong_secret_xx"))).status, 400);
  assert.strictEqual((await handler(stripeEvent(session(), WHSEC, Math.floor(Date.now() / 1000) - 3600))).status, 400);
  assert.strictEqual(grants.length, 0);
});

test("webhook: a paid credit pack grants 50 questions, keyed by the session so retries can't double it", async () => {
  const { handler, grants } = setup();
  assert.strictEqual((await handler(stripeEvent(session()))).status, 200);
  assert.deepStrictEqual(grants[0], { grant_id: "cs_live_abc123", email: "pat@example.com", plan: null, add_credits: 50 });
  // Stripe retries, and the async-payment event for the same session, reuse the same grant ID.
  await handler(stripeEvent(session()));
  await handler(stripeEvent({ ...session(), id: "evt_2", type: "checkout.session.async_payment_succeeded" }));
  assert.deepStrictEqual(grants.map((g) => g.grant_id), ["cs_live_abc123", "cs_live_abc123", "cs_live_abc123"]);
});

test("webhook: nothing is granted until the money has arrived, or for checkouts that aren't ours", async () => {
  const { handler, grants } = setup();
  for (const over of [{ payment_status: "unpaid" }, { metadata: {} }, { metadata: { email: "pat@example.com", product: "gold" } },
    { metadata: { email: "pat@example.com", product: "pro" } } /* a subscription product on a one-time payment */]) {
    const res = await handler(stripeEvent(session(over)));
    assert.strictEqual(res.status, 200); // acknowledged, so Stripe doesn't retry forever
    assert.ok((await res.json()).ignored);
  }
  assert.strictEqual(grants.length, 0);
});

test("webhook: a paid subscription checkout sets the plan", async () => {
  const { handler, grants } = setup();
  await handler(stripeEvent(session({ id: "cs_live_sub1", mode: "subscription", metadata: { email: "Pat@Example.com", product: "starter" } })));
  assert.deepStrictEqual(grants[0], { grant_id: "cs_live_sub1", email: "pat@example.com", plan: "starter", add_credits: 0 });
});

test("webhook: subscription changes follow Stripe's current state, even when events arrive out of order", async () => {
  const { handler, grants, state } = setup({ subscriptions: { sub_1: sub({ items: { data: [{ price: { id: "price_pro" } }] } }) } });
  // An old "updated" event still says starter; the subscription is on pro now.
  await handler(stripeEvent({ id: "evt_old", type: "customer.subscription.updated", data: { object: sub() } }));
  assert.deepStrictEqual(grants[0], { grant_id: "evt_old", email: "pat@example.com", plan: "pro", add_credits: 0 });
  // A failed renewal keeps the plan while Stripe retries the card...
  state.subscriptions.sub_1.status = "past_due";
  await handler(stripeEvent({ id: "evt_pd", type: "customer.subscription.updated", data: { object: sub() } }));
  assert.strictEqual(grants[1].plan, "pro");
  // ...and cancellation (or giving up on the card) ends it.
  state.subscriptions.sub_1.status = "canceled";
  await handler(stripeEvent({ id: "evt_del", type: "customer.subscription.deleted", data: { object: sub() } }));
  assert.deepStrictEqual(grants[2], { grant_id: "evt_del", email: "pat@example.com", plan: "free", add_credits: 0 });
});

test("webhook: a subscription without our metadata falls back to the customer's email; unknown prices are ignored", async () => {
  const { handler, grants } = setup({ customers: [{ id: "cus_1", email: "Pat@Example.com" }],
    subscriptions: { sub_1: sub({ metadata: {} }), sub_2: sub({ id: "sub_2", items: { data: [{ price: { id: "price_other_product" } }] } }) } });
  await handler(stripeEvent({ id: "evt_a", type: "customer.subscription.updated", data: { object: { id: "sub_1" } } }));
  assert.deepStrictEqual(grants[0], { grant_id: "evt_a", email: "pat@example.com", plan: "starter", add_credits: 0 });
  const res = await handler(stripeEvent({ id: "evt_b", type: "customer.subscription.updated", data: { object: { id: "sub_2" } } }));
  assert.strictEqual((await res.json()).ignored, "not a help desk plan");
  assert.strictEqual(grants.length, 1);
});

test("webhook: if the agent can't record the purchase, Stripe is told to retry", async () => {
  const { handler, state, grants } = setup({ agentStatus: 503 });
  assert.strictEqual((await handler(stripeEvent(session()))).status, 500);
  state.agentStatus = 200;
  assert.strictEqual((await handler(stripeEvent(session()))).status, 200);
  assert.strictEqual(grants.length, 2);
});

test("webhook: refunds and disputes alert the owner and never grant anything", async () => {
  const { handler, grants, alerts } = setup();
  await handler(stripeEvent({ id: "evt_r", type: "charge.refunded", data: { object: { id: "ch_1" } } }));
  await handler(stripeEvent({ id: "evt_x", type: "invoice.paid", data: { object: { id: "in_1" } } }));
  assert.deepStrictEqual(alerts, [["Help desk payment: charge.refunded", "Stripe object ch_1"]]);
  assert.strictEqual(grants.length, 0);
});

test("pack size is a setting; a half-configured site refuses to take payments", async () => {
  const big = setup({ env: { ...env, HELPDESK_CREDIT_PACK_SIZE: "120" } });
  await big.handler(stripeEvent(session()));
  assert.strictEqual(big.grants[0].add_credits, 120);
  const missing = setup({ env: { ...env, STRIPE_PRICE_PRO: "" } });
  assert.strictEqual((await missing.handler(post("/api/helpdesk/checkout", { product: "credits" }, { cookie: signedIn() }))).status, 503);
});
