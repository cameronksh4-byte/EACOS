// node --test integrations/website/test/helpdesk-core.test.mjs
import test from "node:test";
import assert from "node:assert";
import crypto from "node:crypto";
import { createHandler, signAgent, newVisitorCookie, readVisitorCookie, makeToken, readToken, parseCookies,
  VISITOR_COOKIE, ACCOUNT_COOKIE } from "../netlify/lib/helpdesk-core.mjs";

const SIGNING = "s".repeat(40), COOKIE = "c".repeat(40);
const env = { HELPDESK_AGENT_URL: "https://helpdesk.example.com", HELPDESK_SIGNING_SECRET: SIGNING,
  HELPDESK_COOKIE_SECRET: COOKIE, HELPDESK_SITE_URL: "https://valleyagentworks.com" };

function setup(overrides = {}) {
  const calls = [], emails = [];
  const answer = overrides.answer || { status: "answered", outcome: "resolved", reply: "1. Reconnect.", used: 1,
    allowance: 5, remaining: 4, credits: 0, signed_in: false, internal_tech_notes: "SECRET NOTE" };
  const handler = createHandler({
    env: overrides.env || env,
    fetch: async (url, init) => {
      calls.push({ url, init, body: JSON.parse(init.body) });
      return new Response(JSON.stringify(overrides.agentBody || answer), { status: overrides.agentStatus || 200 });
    },
    originOk: (req) => req.headers.get("origin") === "https://valleyagentworks.com",
    rateLimit: async () => overrides.rateOk !== false,
    sendEmail: async (m) => { emails.push(m); },
    onSignup: overrides.onSignup,
  });
  return { handler, calls, emails };
}
const post = (path, body, headers = {}) => new Request(`https://valleyagentworks.com${path}`, {
  method: "POST", body: JSON.stringify(body),
  headers: { "content-type": "application/json", origin: "https://valleyagentworks.com", ...headers } });
const cookiesOf = (res) => Object.fromEntries(res.headers.getSetCookie().map((c) => c.split(";")[0].split("=")));

test("signature matches the agent's scheme (same vector as the Python tests)", () => {
  const body = '{"a":2,"b":1}';
  const expected = crypto.createHmac("sha256", "s3cret").update(`1700000000.${body}`).digest("hex");
  assert.strictEqual(signAgent("s3cret", body, 1700000000), `t=1700000000,v1=${expected}`);
});

test("visitor cookies and tokens can't be forged, swapped or outlive their expiry", () => {
  const c = newVisitorCookie(COOKIE);
  const id = readVisitorCookie(COOKIE, c);
  assert.match(id, /^[A-Za-z0-9_-]{16,64}$/);
  assert.strictEqual(readVisitorCookie(COOKIE, `${"x".repeat(24)}.${c.split(".")[1]}`), null);
  assert.strictEqual(readVisitorCookie("other-secret".repeat(4), c), null);
  const link = makeToken(COOKIE, "link", "pat@example.com", 1200, 1_000_000);
  assert.strictEqual(readToken(COOKIE, "link", link, 1_000_000), "pat@example.com");
  assert.strictEqual(readToken(COOKIE, "account", link, 1_000_000), null, "a link token must not work as a session");
  assert.strictEqual(readToken(COOKIE, "link", link, 1_000_000 + 1201 * 1000), null, "expired");
  const forged = `${Buffer.from("boss@example.com").toString("base64url")}.${link.split(".")[1]}.${link.split(".")[2]}`;
  assert.strictEqual(readToken(COOKIE, "link", forged, 1_000_000), null);
  assert.deepStrictEqual(parseCookies("a=1; b=2=3"), { a: "1", b: "2=3" });
});

test("ask: sets a signed HttpOnly visitor cookie, signs the request, uses Netlify's IP, hides internals", async () => {
  const { handler, calls } = setup();
  const res = await handler(post("/api/helpdesk/ask", { message: "VPN won't connect" },
    { "x-forwarded-for": "6.6.6.6" }), { ip: "203.0.113.7" });
  assert.strictEqual(res.status, 200);
  const set = res.headers.getSetCookie()[0];
  assert.match(set, /HttpOnly; Secure; SameSite=Lax/);
  const visitor = readVisitorCookie(COOKIE, cookiesOf(res)[VISITOR_COOKIE]);
  const call = calls[0];
  assert.strictEqual(call.url, "https://helpdesk.example.com/v1/webchat/message");
  assert.deepStrictEqual(call.body, { visitor_id: visitor, client_ip: "203.0.113.7", account_email: null,
    message: "VPN won't connect" });
  const [t, v1] = call.init.headers["X-Agent-Signature"].split(",").map((p) => p.split("=")[1]);
  assert.strictEqual(v1, crypto.createHmac("sha256", SIGNING).update(`${t}.${call.init.body}`).digest("hex"));
  const out = await res.json();
  assert.strictEqual(out.reply, "1. Reconnect.");
  assert.ok(!("internal_tech_notes" in out), "only whitelisted fields reach the browser");
});

test("ask: an existing cookie keeps the same visitor; a forged one is replaced", async () => {
  const { handler, calls } = setup();
  const mine = newVisitorCookie(COOKIE);
  let res = await handler(post("/api/helpdesk/ask", { message: "hello there" }, { cookie: `${VISITOR_COOKIE}=${mine}` }), { ip: "203.0.113.7" });
  assert.strictEqual(calls[0].body.visitor_id, mine.split(".")[0]);
  assert.strictEqual(res.headers.getSetCookie().length, 0);
  res = await handler(post("/api/helpdesk/ask", { message: "hello there" }, { cookie: `${VISITOR_COOKIE}=${"z".repeat(24)}.bad` }), { ip: "203.0.113.7" });
  assert.notStrictEqual(calls[1].body.visitor_id, "z".repeat(24));
  assert.strictEqual(res.headers.getSetCookie().length, 1);
});

test("ask: other sites, bad input, bots and floods never reach the agent", async () => {
  const { handler, calls } = setup();
  assert.strictEqual((await handler(post("/api/helpdesk/ask", { message: "hi there" }, { origin: "https://evil.example" }), { ip: "1.2.3.4" })).status, 403);
  assert.strictEqual((await handler(post("/api/helpdesk/ask", { message: "x".repeat(1001) }), { ip: "1.2.3.4" })).status, 422);
  assert.strictEqual((await handler(post("/api/helpdesk/ask", { message: 42 }), { ip: "1.2.3.4" })).status, 422);
  assert.strictEqual((await handler(post("/api/helpdesk/ask", { message: "hi there", website: "spam" }), { ip: "1.2.3.4" })).status, 200);
  assert.strictEqual((await handler(post("/api/helpdesk/ask", { message: "hi there" }), {})).status, 500, "no trusted IP, no call");
  assert.strictEqual(calls.length, 0);
  const limited = setup({ rateOk: false });
  assert.strictEqual((await limited.handler(post("/api/helpdesk/ask", { message: "hi there" }), { ip: "1.2.3.4" })).status, 429);
});

test("ask: an agent error becomes a friendly 'unavailable', not a stack trace", async () => {
  const { handler } = setup({ agentStatus: 500, agentBody: { detail: "boom" } });
  const out = await (await handler(post("/api/helpdesk/ask", { message: "hi there" }), { ip: "1.2.3.4" })).json();
  assert.strictEqual(out.status, "unavailable");
  assert.ok(!JSON.stringify(out).includes("boom"));
});

test("sign-up: emails a link, verifying it signs the visitor in, and the account email is then sent to the agent", async () => {
  const seen = [];
  const { handler, calls, emails } = setup({ onSignup: async (s) => { seen.push(s); } });
  let res = await handler(post("/api/helpdesk/signup", { email: "Pat@Example.com", name: "Pat", lang: "es" }));
  assert.deepStrictEqual(await res.json(), { ok: true });
  assert.strictEqual(emails[0].to, "pat@example.com");
  const link = emails[0].text.match(/https:\/\/valleyagentworks\.com\/api\/helpdesk\/verify\?\S+/)[0];
  assert.strictEqual(seen.length, 0, "nothing is saved before the address is verified");

  res = await handler(new Request(link), {});
  assert.strictEqual(res.status, 302);
  assert.strictEqual(res.headers.get("location"), "https://valleyagentworks.com/helpdesk/?signup=done");
  assert.deepStrictEqual(seen, [{ email: "pat@example.com", name: "Pat", business: "", lang: "es" }]);
  const account = cookiesOf(res)[ACCOUNT_COOKIE];

  await handler(post("/api/helpdesk/ask", { message: "printer offline" }, { cookie: `${ACCOUNT_COOKIE}=${account}` }), { ip: "203.0.113.7" });
  assert.strictEqual(calls[0].body.account_email, "pat@example.com");

  res = await handler(new Request("https://valleyagentworks.com/api/helpdesk/verify?t=garbage"), {});
  assert.strictEqual(res.headers.get("location"), "https://valleyagentworks.com/helpdesk/?signup=expired");
  assert.strictEqual(res.headers.getSetCookie().length, 0);
  assert.strictEqual((await handler(post("/api/helpdesk/signup", { email: "not-an-email" }))).status, 422);
});

test("a browser cannot claim to be signed in by sending an email itself", async () => {
  const { handler, calls } = setup();
  await handler(post("/api/helpdesk/ask", { message: "hi there", account_email: "boss@example.com" },
    { cookie: `${ACCOUNT_COOKIE}=${Buffer.from("boss@example.com").toString("base64url")}.9999999999.deadbeef` }), { ip: "1.2.3.4" });
  assert.strictEqual(calls[0].body.account_email, null);
});

test("grant: payments reach the agent signed, keyed by the payment id", async () => {
  const { handler, calls } = setup({ agentBody: { applied: true, plan: "starter", credits: 0 } });
  const out = await handler.grant({ grant_id: "pay_123456", email: "pat@example.com", plan: "starter" });
  assert.deepStrictEqual(out.data, { applied: true, plan: "starter", credits: 0 });
  assert.strictEqual(calls[0].url, "https://helpdesk.example.com/v1/webchat/grants");
  assert.deepStrictEqual(calls[0].body, { grant_id: "pay_123456", email: "pat@example.com", plan: "starter", add_credits: 0 });
});

test("refuses to run half-configured (short secrets, http agent URL)", async () => {
  const { handler } = setup({ env: { ...env, HELPDESK_SIGNING_SECRET: "short" } });
  assert.strictEqual((await handler(post("/api/helpdesk/ask", { message: "hi there" }), { ip: "1.2.3.4" })).status, 503);
  const plain = setup({ env: { ...env, HELPDESK_AGENT_URL: "http://helpdesk.example.com" } });
  assert.strictEqual((await plain.handler(post("/api/helpdesk/ask", { message: "hi there" }), { ip: "1.2.3.4" })).status, 503);
});
