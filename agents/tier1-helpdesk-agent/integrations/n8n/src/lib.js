// Shared helpers, prepended to every Code node by build_workflows.py.
// Runs inside n8n's Code node: `$env` and `this.helpers` are provided by n8n.
// Requires NODE_FUNCTION_ALLOW_BUILTIN=crypto and N8N_BLOCK_ENV_ACCESS_IN_NODE=false.
const crypto = require('crypto');

function env(name, fallback) {
  const value = $env[name];
  if (value === undefined || value === null || value === '') {
    if (fallback !== undefined) return fallback;
    throw new Error(`n8n environment variable ${name} is not set`);
  }
  return String(value);
}

function hmacHex(secret, data) {
  return crypto.createHmac('sha256', secret).update(data).digest('hex');
}

function safeEqual(a, b) {
  const x = Buffer.from(String(a));
  const y = Buffer.from(String(b));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}

// Agent <-> n8n signature: "t=<unix>,v1=<hex>", v1 = HMAC-SHA256(secret, "<t>." + rawBody)
function verifyAgentSignature(secret, rawBody, header, maxSkewSeconds = 300, nowSeconds = Date.now() / 1000) {
  if (!secret || !header) return false;
  const parts = {};
  for (const piece of String(header).split(',')) {
    const i = piece.indexOf('=');
    if (i > 0) parts[piece.slice(0, i).trim()] = piece.slice(i + 1).trim();
  }
  const t = Number.parseInt(parts.t, 10);
  if (!Number.isFinite(t) || !parts.v1) return false;
  if (Math.abs(nowSeconds - t) > maxSkewSeconds) return false;
  return safeEqual(hmacHex(secret, `${t}.${rawBody}`), parts.v1);
}

function signAgent(secret, body, nowSeconds = Math.floor(Date.now() / 1000)) {
  return `t=${nowSeconds},v1=${hmacHex(secret, `${nowSeconds}.${body}`)}`;
}

// Slack request signature: "v0=" + HMAC-SHA256(signing secret, "v0:<ts>:" + rawBody)
function verifySlackSignature(secret, rawBody, timestamp, signature, nowSeconds = Date.now() / 1000) {
  if (!secret || !timestamp || !signature) return false;
  const ts = Number.parseInt(timestamp, 10);
  if (!Number.isFinite(ts) || Math.abs(nowSeconds - ts) > 300) return false;
  return safeEqual(`v0=${hmacHex(secret, `v0:${ts}:${rawBody}`)}`, signature);
}

// Raw request body from a Webhook node with the "Raw Body" option on.
async function rawBodyOf(helpers, item, index = 0) {
  if (item.binary && item.binary.data) {
    const buffer = await helpers.getBinaryDataBuffer(index, 'data');
    return buffer.toString('utf8');
  }
  throw new Error('Webhook node must have the "Raw Body" option enabled');
}

function header(item, name) {
  const headers = (item.json && item.json.headers) || {};
  return headers[name.toLowerCase()] || '';
}

function slackBase() {
  return env('SLACK_API_BASE', 'https://slack.com/api').replace(/\/+$/, '');
}

async function slackApi(helpers, method, payload) {
  const res = await helpers.httpRequest({
    method: 'POST',
    url: `${slackBase()}/${method}`,
    headers: { Authorization: `Bearer ${env('SLACK_BOT_TOKEN')}`, 'Content-Type': 'application/json; charset=utf-8' },
    body: payload,
    json: true,
  });
  if (!res || res.ok !== true) throw new Error(`Slack ${method} failed: ${res && res.error}`);
  return res;
}

async function slackGet(helpers, method, qs) {
  const res = await helpers.httpRequest({
    method: 'GET',
    url: `${slackBase()}/${method}`,
    headers: { Authorization: `Bearer ${env('SLACK_BOT_TOKEN')}` },
    qs,
    json: true,
  });
  if (!res || res.ok !== true) throw new Error(`Slack ${method} failed: ${res && res.error}`);
  return res;
}

async function slackUserIdByEmail(helpers, email) {
  const res = await slackGet(helpers, 'users.lookupByEmail', { email });
  return res.user.id;
}

async function slackEmailOf(helpers, userId) {
  const res = await slackGet(helpers, 'users.info', { user: userId });
  const email = res.user && res.user.profile && res.user.profile.email;
  if (!email) throw new Error('Slack user has no email (add the users:read.email scope)');
  return email.toLowerCase();
}

async function dm(helpers, email, text, blocks) {
  const channel = await slackUserIdByEmail(helpers, email);
  return slackApi(helpers, 'chat.postMessage', { channel, text, ...(blocks ? { blocks } : {}) });
}

async function postToChannel(helpers, channel, text, blocks) {
  return slackApi(helpers, 'chat.postMessage', { channel, text, ...(blocks ? { blocks } : {}) });
}

// n8n's Code-node sandbox has no URLSearchParams, so encode/decode forms by hand.
function formEncode(fields) {
  return Object.entries(fields)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join('&');
}

function formDecode(raw) {
  const out = {};
  for (const pair of String(raw || '').split('&')) {
    if (!pair) continue;
    const i = pair.indexOf('=');
    const key = decodeURIComponent((i < 0 ? pair : pair.slice(0, i)).replace(/\+/g, ' '));
    out[key] = i < 0 ? '' : decodeURIComponent(pair.slice(i + 1).replace(/\+/g, ' '));
  }
  return out;
}

function truncate(text, max) {
  const s = String(text || '');
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}
