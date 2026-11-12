// Code node: turn a verified button click into a signed callback to the agent, then
// update the Slack message with the outcome.
//   approval_approve / approval_deny -> POST {AGENT_BASE_URL}/v1/callbacks/approval
//   sop_ingest / sop_reject          -> POST {AGENT_BASE_URL}/v1/callbacks/sop-draft
// The clicker's identity comes from Slack (users.info), never from the button value,
// and the agent enforces "only the designated approver".
const helpers = this.helpers;
const click = $input.first().json;
if (!click.ok) return [];

const base = env('AGENT_BASE_URL').replace(/\/+$/, '');
const email = await slackEmailOf(helpers, click.user_id);
let path;
let body;
if (click.value.k === 'approval') {
  path = '/v1/callbacks/approval';
  body = {
    approval_id: click.value.a,
    callback_nonce: click.value.n,
    tenant_id: click.value.t,
    decision: click.value.d,
    decided_by_email: email,
  };
} else if (click.value.k === 'sop') {
  path = '/v1/callbacks/sop-draft';
  body = { tenant_id: click.value.t, draft_id: click.value.d, decision: click.value.x, reviewer_email: email };
} else {
  return [{ json: { ignored: click.action_id } }];
}

const raw = JSON.stringify(body);
let status;
try {
  const res = await helpers.httpRequest({
    method: 'POST',
    url: `${base}${path}`,
    headers: { 'Content-Type': 'application/json', 'X-Agent-Signature': signAgent(env('N8N_CALLBACK_SECRET'), raw) },
    body: raw,
    returnFullResponse: true,
    ignoreHttpStatusErrors: true,
  });
  status = res.statusCode;
} catch (err) {
  status = 0;
}

const who = `<@${click.user_id}>`;
const messages = {
  approval: {
    200: body.decision === 'approved' ? `✅ Approved by ${who}. Setup has started.` : `❌ Denied by ${who}.`,
    409: '⚠️ This request was already decided, has expired, or is waiting on someone else.',
    403: '⚠️ This request belongs to a tenant this help desk doesn\'t serve.',
  },
  sop: {
    200: body.decision === 'approve' ? `✅ Ingested into the knowledge base by ${who}.` : `🗑️ Rejected by ${who}.`,
    409: '⚠️ This draft was already reviewed.',
    403: '⚠️ This draft belongs to a tenant this help desk doesn\'t serve.',
  },
}[click.value.k];
const text = messages[status] || `⚠️ Couldn't record that (status ${status}). Please try again or tell IT.`;

if (click.response_url) {
  await helpers.httpRequest({
    method: 'POST',
    url: click.response_url,
    body: { replace_original: status === 200, text },
    json: true,
  });
}
return [{ json: { status, path } }];
