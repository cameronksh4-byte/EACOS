// Website side of the help desk free trial. No imports besides node:crypto, so it
// runs anywhere and is unit-tested as is (integrations/website/test/).
//
//   browser ──► this handler (your website's server) ──signed──► help desk agent
//
// The browser never talks to the agent, and never holds a secret. This handler:
//   - gives each visitor a signed, HttpOnly cookie ID (the agent counts 5 free questions per ID,
//     plus a daily cap per network, so clearing cookies doesn't restart the trial)
//   - signs every request to the agent with HELPDESK_SIGNING_SECRET
//   - runs sign-up: emails a magic link, and only a verified address counts as an account
//   - passes on only reply text and counters
//
// createHandler(deps) returns a Netlify Functions v2 handler: (Request, context) => Response.
import crypto from "node:crypto";

export const PATHS = ["/api/helpdesk/ask", "/api/helpdesk/signup", "/api/helpdesk/verify", "/api/helpdesk/me",
  "/api/helpdesk/signout"];
export const VISITOR_COOKIE = "vaw_hd_v";
export const ACCOUNT_COOKIE = "vaw_hd_a";
export const MAX_MESSAGE_CHARS = 1000;
const VISITOR_TTL_S = 365 * 24 * 3600;
const ACCOUNT_TTL_S = 30 * 24 * 3600;
const LINK_TTL_S = 20 * 60;
const EMAIL_RE = /^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;
// What the browser may see of the agent's answer.
const PUBLIC_FIELDS = ["status", "outcome", "reply", "cited_sop_ids", "signed_in", "plan", "used", "allowance",
  "remaining", "credits", "signup_url", "upgrade_url"];

const b64u = (s) => Buffer.from(s, "utf8").toString("base64url");
const unb64u = (s) => Buffer.from(String(s), "base64url").toString("utf8");
const hmac = (secret, value) => crypto.createHmac("sha256", secret).update(value).digest("hex");
function safeEqual(a, b) {
  const x = Buffer.from(String(a)), y = Buffer.from(String(b));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}

// Same scheme the agent verifies (app/services/signing.py):
//   X-Agent-Signature: t=<unix seconds>,v1=hex(HMAC-SHA256(secret, "<t>." + raw body))
export function signAgent(secret, body, timestamp = Math.floor(Date.now() / 1000)) {
  return `t=${timestamp},v1=${hmac(secret, `${timestamp}.${body}`)}`;
}

export function newVisitorCookie(secret) {
  const id = crypto.randomBytes(18).toString("base64url"); // 24 chars of [A-Za-z0-9_-]
  return `${id}.${hmac(secret, `visitor:${id}`)}`;
}
export function readVisitorCookie(secret, value) {
  const [id, mac] = String(value || "").split(".");
  if (!id || !mac || !/^[A-Za-z0-9_-]{16,64}$/.test(id)) return null;
  return safeEqual(mac, hmac(secret, `visitor:${id}`)) ? id : null;
}

// kind "link" = the emailed magic link (short-lived); "account" = the session cookie.
export function makeToken(secret, kind, email, ttlSeconds, now = Date.now()) {
  const exp = Math.floor(now / 1000) + ttlSeconds;
  const payload = `${b64u(email)}.${exp}`;
  return `${payload}.${hmac(secret, `${kind}:${payload}`)}`;
}
export function readToken(secret, kind, token, now = Date.now()) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) return null;
  const [emailPart, exp, mac] = parts;
  if (!safeEqual(mac, hmac(secret, `${kind}:${emailPart}.${exp}`))) return null;
  if (!/^\d{1,12}$/.test(exp) || Number(exp) * 1000 < now) return null;
  const email = unb64u(emailPart).toLowerCase();
  return EMAIL_RE.test(email) && email.length <= 320 ? email : null;
}

export function parseCookies(header) {
  const out = {};
  for (const part of String(header || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return out;
}
const cookie = (name, value, maxAge) =>
  `${name}=${value}; Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Lax`;

function json(obj, status = 200, cookies = []) {
  const headers = new Headers({ "Content-Type": "application/json", "Cache-Control": "no-store" });
  for (const c of cookies) headers.append("Set-Cookie", c);
  return new Response(JSON.stringify(obj), { status, headers });
}

// Signed calls to the agent. Also used by the billing handler (helpdesk-stripe.mjs).
export function createAgentClient({ env, fetch }) {
  const agentUrl = String(env.HELPDESK_AGENT_URL || "").replace(/\/+$/, "");
  const signingSecret = env.HELPDESK_SIGNING_SECRET || "";

  async function agent(path, payload) {
    const body = JSON.stringify(payload);
    const res = await fetch(`${agentUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Agent-Signature": signAgent(signingSecret, body) },
      body,
      signal: AbortSignal.timeout(25000),
    });
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    return { status: res.status, data };
  }

  // Plan change and/or credits, once a payment is confirmed (server side only).
  // grant_id must be the payment's own ID, so a retried payment webhook can't grant twice.
  async function grant({ grant_id, email, plan = null, add_credits = 0 }) {
    return agent("/v1/webchat/grants", { grant_id, email, plan, add_credits });
  }

  return { agent, grant };
}

export const accountFromCookies = (cookieSecret, cookieHeader) =>
  readToken(cookieSecret, "account", parseCookies(cookieHeader)[ACCOUNT_COOKIE]);

/**
 * deps:
 *   env        { HELPDESK_AGENT_URL, HELPDESK_SIGNING_SECRET, HELPDESK_COOKIE_SECRET, HELPDESK_SITE_URL }
 *   fetch      fetch implementation (the global one)
 *   originOk   (req) => boolean          same-site check for POSTs (CSRF)
 *   rateLimit  async (req, bucket, max, windowSeconds) => boolean
 *   sendEmail  async ({ to, subject, html, text }) => any
 *   linkEmail  optional ({ link, lang }) => { subject, html, text }   (your branded email)
 *   onSignup   optional async ({ email, name, business, lang }) => void   (e.g. save a lead; only
 *              called once the address is verified)
 */
export function createHandler(deps) {
  const env = deps.env || {};
  const agentUrl = String(env.HELPDESK_AGENT_URL || "").replace(/\/+$/, "");
  const signingSecret = env.HELPDESK_SIGNING_SECRET || "";
  const cookieSecret = env.HELPDESK_COOKIE_SECRET || "";
  const siteUrl = String(env.HELPDESK_SITE_URL || "").replace(/\/+$/, "");
  const configured = agentUrl.startsWith("https://") && signingSecret.length >= 32 && cookieSecret.length >= 32;

  const { agent, grant } = createAgentClient({ env, fetch: deps.fetch });

  const visitorOf = (cookies) => readVisitorCookie(cookieSecret, cookies[VISITOR_COOKIE]);
  const accountOf = (cookies) => readToken(cookieSecret, "account", cookies[ACCOUNT_COOKIE]);

  async function ask(req, context, cookies) {
    if (!(await deps.rateLimit(req, "helpdesk-ask", 60, 3600))) return json({ error: "rate_limited" }, 429);
    let body;
    try { body = await req.json(); } catch { return json({ error: "bad_request" }, 400); }
    if (body && body.website) return json({ status: "answered", reply: "Thanks!" }); // honeypot: bots only
    const message = typeof body?.message === "string" ? body.message.trim() : "";
    if (message.length < 2 || message.length > MAX_MESSAGE_CHARS) return json({ error: "message_length" }, 422);

    const setCookies = [];
    let visitor = visitorOf(cookies);
    if (!visitor) {
      const fresh = newVisitorCookie(cookieSecret);
      setCookies.push(cookie(VISITOR_COOKIE, fresh, VISITOR_TTL_S));
      visitor = readVisitorCookie(cookieSecret, fresh);
    }
    // The address as Netlify saw it. Never take it from a header the browser can set.
    const clientIp = context && context.ip;
    if (!clientIp) return json({ error: "server" }, 500);

    const { status, data } = await agent("/v1/webchat/message", {
      visitor_id: visitor, client_ip: clientIp, account_email: accountOf(cookies), message,
    });
    if (status !== 200 || !data) {
      return json({ status: "unavailable", reply: "Sorry, the help desk assistant isn't available right now. Please try again in a little while." }, 200, setCookies);
    }
    const out = {};
    for (const key of PUBLIC_FIELDS) if (key in data) out[key] = data[key];
    return json(out, 200, setCookies);
  }

  async function signup(req) {
    if (!(await deps.rateLimit(req, "helpdesk-signup", 5, 3600))) return json({ error: "rate_limited" }, 429);
    let body;
    try { body = await req.json(); } catch { return json({ error: "bad_request" }, 400); }
    const email = typeof body?.email === "string" ? body.email.trim().toLowerCase() : "";
    if (!EMAIL_RE.test(email) || email.length > 320) return json({ error: "email" }, 422);
    const lang = body.lang === "es" ? "es" : "en";
    // One link per address every 2 minutes, so the form can't be used to flood someone's inbox.
    if (deps.rateLimitKey && !(await deps.rateLimitKey(`helpdesk-link:${hmac(cookieSecret, email)}`, 1, 120))) {
      return json({ ok: true });
    }
    const profile = b64u(JSON.stringify({
      name: String(body.name || "").slice(0, 120), business: String(body.business || "").slice(0, 160), lang }));
    const link = `${siteUrl}/api/helpdesk/verify?t=${encodeURIComponent(makeToken(cookieSecret, "link", email, LINK_TTL_S))}` +
      `&p=${profile}`;
    const mail = deps.linkEmail ? deps.linkEmail({ link, lang }) : {
      subject: "Confirm your email to keep using the IT help desk",
      text: `Open this link within 20 minutes to finish signing up:\n\n${link}\n\nIf you didn't ask for this, ignore this email.`,
      html: `<p>Open this link within 20 minutes to finish signing up:</p><p><a href="${link}">Confirm my email</a></p>` +
        "<p>If you didn't ask for this, ignore this email.</p>",
    };
    try {
      await deps.sendEmail({ to: email, ...mail });
    } catch (e) {
      console.error("helpdesk signup email failed", e && e.message);
    }
    return json({ ok: true }); // the same answer whether or not the address exists or the email sent
  }

  async function verify(req) {
    const url = new URL(req.url);
    const email = readToken(cookieSecret, "link", url.searchParams.get("t"));
    const headers = new Headers({ "Cache-Control": "no-store" });
    if (!email) {
      headers.set("Location", `${siteUrl}/helpdesk/?signup=expired`);
      return new Response(null, { status: 302, headers });
    }
    if (deps.onSignup) {
      let profile = {};
      try { profile = JSON.parse(unb64u(url.searchParams.get("p") || "")); } catch { /* optional */ }
      try {
        await deps.onSignup({ email, name: String(profile.name || "").slice(0, 120),
          business: String(profile.business || "").slice(0, 160), lang: profile.lang === "es" ? "es" : "en" });
      } catch (e) {
        console.error("helpdesk onSignup failed", e && e.message);
      }
    }
    headers.append("Set-Cookie", cookie(ACCOUNT_COOKIE, makeToken(cookieSecret, "account", email, ACCOUNT_TTL_S), ACCOUNT_TTL_S));
    headers.set("Location", `${siteUrl}/helpdesk/?signup=done`);
    return new Response(null, { status: 302, headers });
  }

  async function me(cookies) {
    const email = accountOf(cookies);
    if (!email) return json({ signed_in: false });
    const { status, data } = await agent("/v1/webchat/account", { email });
    if (status === 200 && data) return json({ signed_in: true, ...data });
    return json({ signed_in: true, email, plan: "free", used: 0, credits: 0 }); // no question asked yet
  }

  const handler = async (req, context) => {
    const url = new URL(req.url);
    if (!configured) return json({ error: "not_configured" }, 503);
    const cookies = parseCookies(req.headers.get("cookie"));
    try {
      if (url.pathname === "/api/helpdesk/verify" && req.method === "GET") return await verify(req);
      if (url.pathname === "/api/helpdesk/me" && req.method === "GET") return await me(cookies);
      if (req.method !== "POST") return json({ error: "method" }, 405);
      if (!deps.originOk(req)) return json({ error: "forbidden" }, 403);
      if (url.pathname === "/api/helpdesk/ask") return await ask(req, context, cookies);
      if (url.pathname === "/api/helpdesk/signup") return await signup(req);
      if (url.pathname === "/api/helpdesk/signout") return json({ ok: true }, 200, [cookie(ACCOUNT_COOKIE, "", 0)]);
      return json({ error: "not_found" }, 404);
    } catch (e) {
      console.error("helpdesk error", e && e.message);
      return json({ error: "server" }, 500);
    }
  };
  handler.grant = grant;
  return handler;
}
