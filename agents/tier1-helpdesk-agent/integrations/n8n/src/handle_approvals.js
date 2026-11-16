// Code node: act on approval/provisioning events from the agent.
//   approval.requested     -> DM the approver an interactive card, tell the requester
//   provisioning.requested -> add the requester to the item's group (Entra ID / Okta)
//   approval.denied        -> tell the requester
//
// Output: one item { ok, status, ... } for the Respond node, which answers the
// agent AFTER the work is done. The agent treats 2xx as "delivered", so 200 is
// only returned once Slack (or the directory) accepted the request; any failure
// is a 502 and the agent retries or escalates.
const helpers = this.helpers;
const input = $input.first().json;
if (!input.ok || !input.envelope) return [{ json: { ok: false, status: input.status || 401 } }];
const envelope = input.envelope;
const event = envelope.event;

async function graphToken() {
  const res = await helpers.httpRequest({
    method: 'POST',
    url: `https://login.microsoftonline.com/${env('GRAPH_TENANT_ID')}/oauth2/v2.0/token`,
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: formEncode({
      grant_type: 'client_credentials',
      client_id: env('GRAPH_CLIENT_ID'),
      client_secret: env('GRAPH_CLIENT_SECRET'),
      scope: 'https://graph.microsoft.com/.default',
    }),
    json: true,
  });
  return res.access_token;
}

async function addToEntraGroup(groupId, email) {
  const token = await graphToken();
  const auth = { Authorization: `Bearer ${token}` };
  const user = await helpers.httpRequest({
    method: 'GET',
    url: `https://graph.microsoft.com/v1.0/users/${encodeURIComponent(email)}`,
    qs: { $select: 'id' },
    headers: auth,
    json: true,
  });
  // Ask for the full response: n8n's thrown HTTP errors don't carry the body,
  // and Graph reports "already a member" as a 400 with a message.
  const res = await helpers.httpRequest({
    method: 'POST',
    url: `https://graph.microsoft.com/v1.0/groups/${encodeURIComponent(groupId)}/members/$ref`,
    headers: { ...auth, 'Content-Type': 'application/json' },
    body: { '@odata.id': `https://graph.microsoft.com/v1.0/directoryObjects/${user.id}` },
    json: true,
    returnFullResponse: true,
    ignoreHttpStatusErrors: true,
  });
  const status = Number(res && res.statusCode);
  if (status >= 200 && status < 300) return;
  const body = res && res.body;
  const message = (body && body.error && body.error.message) || (typeof body === 'string' ? body : '') || '';
  if (status === 400 && /already exist/i.test(message)) return; // already a member counts as success
  throw new Error(`Graph add-to-group failed: ${status} ${truncate(message, 200)}`);
}

async function addToOktaGroup(groupId, email) {
  const auth = { Authorization: `SSWS ${env('OKTA_API_TOKEN')}`, Accept: 'application/json' };
  const base = `https://${env('OKTA_DOMAIN')}`;
  const user = await helpers.httpRequest({
    method: 'GET', url: `${base}/api/v1/users/${encodeURIComponent(email)}`, headers: auth, json: true,
  });
  await helpers.httpRequest({
    method: 'PUT',
    url: `${base}/api/v1/groups/${encodeURIComponent(groupId)}/users/${encodeURIComponent(user.id)}`,
    headers: auth,
  });
}

function approvalCard(e) {
  const cost = e.license_cost_usd ? ` · $${Number(e.license_cost_usd).toFixed(2)}/month` : '';
  const value = (decision) => JSON.stringify({
    k: 'approval', a: e.approval_id, n: e.callback_nonce, t: e.tenant_id, d: decision,
  });
  return [
    {
      type: 'section',
      text: {
        type: 'mrkdwn',
        text: `*Approval needed:* ${slackEscape(e.item_display_name)}${cost}\n` +
          `*Requested by:* ${slackEscape(e.requester_email)}\n*Ticket:* ${slackEscape(e.ticket_id)}`,
      },
    },
    ...(e.justification ? [{
      type: 'section', text: { type: 'mrkdwn', text: `*Reason:*\n>${slackEscape(truncate(e.justification, 1500)).replace(/\n/g, '\n>')}` },
    }] : []),
    {
      type: 'context',
      elements: [{ type: 'mrkdwn', text: `Expires ${new Date(e.expires_at).toUTCString()}` }],
    },
    {
      type: 'actions',
      block_id: `approval:${e.approval_id}`,
      elements: [
        {
          type: 'button', action_id: 'approval_approve', style: 'primary',
          text: { type: 'plain_text', text: 'Approve' }, value: value('approved'),
          confirm: {
            title: { type: 'plain_text', text: 'Approve this request?' },
            text: { type: 'mrkdwn', text: `${slackEscape(e.requester_email)} will get ${slackEscape(e.item_display_name)}.` },
            confirm: { type: 'plain_text', text: 'Approve' },
            deny: { type: 'plain_text', text: 'Cancel' },
          },
        },
        {
          type: 'button', action_id: 'approval_deny', style: 'danger',
          text: { type: 'plain_text', text: 'Deny' }, value: value('denied'),
        },
      ],
    },
  ];
}

async function handle() {
  if (event === 'approval.requested') {
    await dm(helpers, envelope.approver_email,
      `Approval needed: ${envelope.item_display_name} for ${envelope.requester_email}`, approvalCard(envelope));
    return { done: event, approval_id: envelope.approval_id };
  }

  if (event === 'provisioning.requested') {
    const provider = env('PROVISIONING_PROVIDER', 'none').toLowerCase();
    const group = envelope.provisioning_group;
    try {
      if (!group || provider === 'none') {
        throw new Error(group ? 'PROVISIONING_PROVIDER is none' : 'catalog item has no provisioning_group');
      }
      if (provider === 'entra') await addToEntraGroup(group, envelope.requester_email);
      else if (provider === 'okta') await addToOktaGroup(group, envelope.requester_email);
      else throw new Error(`unknown PROVISIONING_PROVIDER ${provider}`);
    } catch (err) {
      // Technicians finish it by hand. If even this alert can't be posted, the
      // error propagates, the agent gets a 502 and escalates the ticket itself.
      await postToChannel(helpers, env('SLACK_ALERT_CHANNEL'),
        `:warning: Manual provisioning needed for ${slackEscape(envelope.tenant_id)}: add ` +
        `${slackEscape(envelope.requester_email)} to ${slackEscape(envelope.item_display_name)} ` +
        `(group ${slackEscape(group || 'n/a')}). Ticket ${slackEscape(envelope.ticket_id)}. ` +
        `Reason: ${slackEscape(truncate(err.message, 300))}`);
      return { done: event, provisioned: false, error: err.message };
    }
    // The access is granted. Telling the requester is a courtesy: a person who
    // isn't in Slack (a Jira or Teams ticket) must not turn success into an alert.
    let requester_notified = true;
    try {
      await dm(helpers, envelope.requester_email,
        `✅ ${slackEscape(envelope.item_display_name)} is being set up for you. It usually appears within ` +
        `the hour. (Ticket ${slackEscape(envelope.ticket_id)})`);
    } catch (err) {
      requester_notified = false;
    }
    return { done: event, provisioned: true, requester_notified };
  }

  if (event === 'approval.denied') {
    const note = envelope.decision_note
      ? ` Note from your manager: "${slackEscape(truncate(envelope.decision_note, 500))}"` : '';
    await dm(helpers, envelope.requester_email,
      `Your request for ${slackEscape(envelope.item_display_name)} wasn't approved.${note} ` +
      `Reply to the help desk if you have questions. (Ticket ${slackEscape(envelope.ticket_id)})`);
    return { done: event };
  }

  return { ignored: event };
}

try {
  return [{ json: { ok: true, status: 200, ...(await handle()) } }];
} catch (err) {
  return [{ json: { ok: false, status: 502, done: event, error: truncate(String((err && err.message) || err), 300) } }];
}
