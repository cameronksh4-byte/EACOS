// Unit tests for the n8n Code-node sources, run exactly as n8n runs them
// (lib.js + handler, as one async function with `this.helpers`, `$input`, `$env`).
//   node --test integrations/n8n/test/
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const SRC = path.join(__dirname, '..', 'src');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function load(handler) {
  const code = fs.readFileSync(path.join(SRC, 'lib.js'), 'utf8') + '\n' + fs.readFileSync(path.join(SRC, handler), 'utf8');
  return new AsyncFunction('$input', '$env', 'require', code);
}

function fakeHelpers(routes) {
  const calls = [];
  return {
    calls,
    async httpRequest(opts) {
      calls.push(opts);
      for (const [match, reply] of routes) {
        if (match(opts)) return typeof reply === 'function' ? reply(opts) : reply;
      }
      throw new Error(`unexpected request ${opts.method} ${opts.url}`);
    },
    async getBinaryDataBuffer() {
      throw new Error('not used');
    },
  };
}

const input = (json) => ({ first: () => ({ json }), all: () => [{ json }] });
const baseEnv = { SLACK_BOT_TOKEN: 'xoxb', SLACK_ALERT_CHANNEL: 'C_ALERT' };
const slackOk = [(o) => o.url.includes('slack.com/api/users.lookupByEmail'), { ok: true, user: { id: 'U_PAT' } }];
const slackPost = [(o) => o.url.includes('slack.com/api/chat.postMessage'), { ok: true }];

const provisioning = {
  ok: true,
  envelope: {
    event: 'provisioning.requested', tenant_id: 'acme-corp', ticket_id: 'T-1', requester_email: "o'brien@acme.com",
    item_display_name: 'Figma', provisioning_group: 'grp-123',
  },
};

test('Entra ID provisioning: token, user lookup, add to group, DM requester', async () => {
  const helpers = fakeHelpers([
    [(o) => o.url.includes('login.microsoftonline.com/tid/oauth2/v2.0/token'), { access_token: 'tok' }],
    [(o) => o.method === 'GET' && o.url.includes('graph.microsoft.com/v1.0/users/'), { id: 'user-guid' }],
    [(o) => o.method === 'POST' && o.url.endsWith('/groups/grp-123/members/$ref'), {}],
    slackOk, slackPost,
  ]);
  const env = { ...baseEnv, PROVISIONING_PROVIDER: 'entra', GRAPH_TENANT_ID: 'tid', GRAPH_CLIENT_ID: 'cid',
    GRAPH_CLIENT_SECRET: 's&cret=1' };
  const out = await load('handle_approvals.js').call({ helpers }, input(provisioning), env, require);
  assert.deepStrictEqual(out[0].json, { done: 'provisioning.requested', provisioned: true });
  const token = helpers.calls[0];
  assert.match(token.body, /client_secret=s%26cret%3D1/); // form-encoded without URLSearchParams
  assert.match(token.body, /grant_type=client_credentials/);
  assert.ok(helpers.calls[1].url.endsWith(encodeURIComponent("o'brien@acme.com")));
  const add = helpers.calls[2];
  assert.strictEqual(add.headers.Authorization, 'Bearer tok');
  assert.deepStrictEqual(add.body, { '@odata.id': 'https://graph.microsoft.com/v1.0/directoryObjects/user-guid' });
  assert.ok(helpers.calls.some((c) => c.url.includes('chat.postMessage') && /being set up/.test(c.body.text)));
});

test('Entra ID: already a member counts as success', async () => {
  const helpers = fakeHelpers([
    [(o) => o.url.includes('/token'), { access_token: 'tok' }],
    [(o) => o.method === 'GET' && o.url.includes('graph.microsoft.com'), { id: 'u' }],
    [(o) => o.url.endsWith('$ref'), () => { throw Object.assign(new Error('400'), { response: { body: 'One or more added object references already exist' } }); }],
    slackOk, slackPost,
  ]);
  const env = { ...baseEnv, PROVISIONING_PROVIDER: 'entra', GRAPH_TENANT_ID: 't', GRAPH_CLIENT_ID: 'c', GRAPH_CLIENT_SECRET: 's' };
  const out = await load('handle_approvals.js').call({ helpers }, input(provisioning), env, require);
  assert.strictEqual(out[0].json.provisioned, true);
});

test('Okta provisioning: user lookup then PUT group membership', async () => {
  const helpers = fakeHelpers([
    [(o) => o.method === 'GET' && o.url.startsWith('https://acme.okta.com/api/v1/users/'), { id: '00u1' }],
    [(o) => o.method === 'PUT' && o.url === 'https://acme.okta.com/api/v1/groups/grp-123/users/00u1', ''],
    slackOk, slackPost,
  ]);
  const env = { ...baseEnv, PROVISIONING_PROVIDER: 'okta', OKTA_DOMAIN: 'acme.okta.com', OKTA_API_TOKEN: 'ssws' };
  const out = await load('handle_approvals.js').call({ helpers }, input(provisioning), env, require);
  assert.strictEqual(out[0].json.provisioned, true);
  assert.strictEqual(helpers.calls[1].headers.Authorization, 'SSWS ssws');
});

test('provisioning failure alerts technicians instead of failing silently', async () => {
  const helpers = fakeHelpers([
    [(o) => o.url.includes('/token'), () => { throw new Error('AADSTS7000215 invalid client secret'); }],
    slackPost,
  ]);
  const env = { ...baseEnv, PROVISIONING_PROVIDER: 'entra', GRAPH_TENANT_ID: 't', GRAPH_CLIENT_ID: 'c', GRAPH_CLIENT_SECRET: 's' };
  const out = await load('handle_approvals.js').call({ helpers }, input(provisioning), env, require);
  assert.strictEqual(out[0].json.provisioned, false);
  const alert = helpers.calls.find((c) => c.url.includes('chat.postMessage'));
  assert.strictEqual(alert.body.channel, 'C_ALERT');
  assert.match(alert.body.text, /Manual provisioning needed.*AADSTS7000215/);
});

test('handlers do nothing for unverified input', async () => {
  const helpers = fakeHelpers([]);
  for (const h of ['handle_approvals.js', 'handle_notifications.js', 'handle_slack_action.js']) {
    const out = await load(h).call({ helpers }, input({ ok: false }), baseEnv, require);
    assert.deepStrictEqual(out, [], h);
  }
  assert.strictEqual(helpers.calls.length, 0);
});

test('signature helpers match the agent format and reject tampering/replay', async () => {
  const lib = fs.readFileSync(path.join(SRC, 'lib.js'), 'utf8');
  const fn = new Function('$env', 'require', `${lib}; return { signAgent, verifyAgentSignature, verifySlackSignature, formDecode };`);
  const { signAgent, verifyAgentSignature, verifySlackSignature, formDecode } = fn({}, require);
  const body = '{"a":2,"b":1}';
  // Vector produced by the Python agent: signing.sign("s3cret", body, timestamp=1700000000)
  const expected = crypto.createHmac('sha256', 's3cret').update(`1700000000.${body}`).digest('hex');
  assert.strictEqual(signAgent('s3cret', body, 1700000000), `t=1700000000,v1=${expected}`);
  const header = signAgent('s3cret', body, 1700000000);
  assert.ok(verifyAgentSignature('s3cret', body, header, 300, 1700000100));
  assert.ok(!verifyAgentSignature('s3cret', body + ' ', header, 300, 1700000100));
  assert.ok(!verifyAgentSignature('wrong', body, header, 300, 1700000100));
  assert.ok(!verifyAgentSignature('s3cret', body, header, 300, 1700001000));
  assert.ok(!verifyAgentSignature('s3cret', body, 'garbage', 300, 1700000100));

  const slackSig = 'v0=' + crypto.createHmac('sha256', 'ss').update('v0:1700000000:payload=x').digest('hex');
  assert.ok(verifySlackSignature('ss', 'payload=x', '1700000000', slackSig, 1700000010));
  assert.ok(!verifySlackSignature('ss', 'payload=y', '1700000000', slackSig, 1700000010));
  assert.ok(!verifySlackSignature('ss', 'payload=x', '1700000000', slackSig, 1700001000));

  assert.deepStrictEqual(formDecode('payload=%7B%22a%22%3A%22b+c%22%7D&x=1+2'), { payload: '{"a":"b c"}', x: '1 2' });
});
