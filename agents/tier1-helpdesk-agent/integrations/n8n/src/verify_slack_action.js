// Code node: verify a Slack interactivity request (button click) and parse it.
// Slack needs a reply within 3 seconds, so the Respond node answers right after this,
// and the slow work happens in the next node.
const helpers = this.helpers;
const item = $input.first();
const raw = await rawBodyOf(helpers, item);
const ok = verifySlackSignature(
  env('SLACK_SIGNING_SECRET'), raw, header(item, 'x-slack-request-timestamp'), header(item, 'x-slack-signature'),
);
if (!ok) return [{ json: { ok: false, status: 401 } }];

const payload = JSON.parse(formDecode(raw).payload || '{}');
const action = (payload.actions || [])[0];
if (payload.type !== 'block_actions' || !action) return [{ json: { ok: false, status: 200, ignored: true } }];

let value;
try {
  value = JSON.parse(action.value || '{}');
} catch (e) {
  return [{ json: { ok: false, status: 200, ignored: true } }];
}
return [{
  json: {
    ok: true,
    status: 200,
    action_id: action.action_id,
    value,
    user_id: payload.user && payload.user.id,
    response_url: payload.response_url,
  },
}];
