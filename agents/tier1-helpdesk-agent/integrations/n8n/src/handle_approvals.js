// Code node: act on approval/provisioning events from the agent.
//   approval.requested     -> DM the approver an interactive card, tell the requester
//   provisioning.requested -> add the requester to the item's group (Entra ID / Okta)
//   approval.denied        -> tell the requester
const helpers = this.helpers;
const { ok, envelope } = $input.first().json;
if (!ok || !envelope) return [];
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
  try {
    await helpers.httpRequest({
      method: 'POST',
      url: `https://graph.microsoft.com/v1.0/groups/${encodeURIComponent(groupId)}/members/$ref`,
      headers: { ...auth, 'Content-Type': 'application/json' },
      body: { '@odata.id': `https://graph.microsoft.com/v1.0/directoryObjects/${user.id}` },
      json: true,
    });
  } catch (err) {
    // Already a member counts as success.
    const text = JSON.stringify((err && (err.response || err.cause || err.message)) || '');
    if (!/already exist/i.test(text)) throw err;
  }
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
        text: `*Approval needed:* ${e.item_display_name}${cost}\n*Requested by:* ${e.requester_email}\n` +
          `*Ticket:* ${e.ticket_id}`,
      },
    },
    ...(e.justification ? [{
      type: 'section', text: { type: 'mrkdwn', text: `*Reason:*\n>${truncate(e.justification, 1500)}` },
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
            text: { type: 'mrkdwn', text: `${e.requester_email} will get ${e.item_display_name}.` },
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

if (event === 'approval.requested') {
  await dm(helpers, envelope.approver_email,
    `Approval needed: ${envelope.item_display_name} for ${envelope.requester_email}`, approvalCard(envelope));
  return [{ json: { done: event, approval_id: envelope.approval_id } }];
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
    await dm(helpers, envelope.requester_email,
      `✅ ${envelope.item_display_name} is being set up for you. It usually appears within the hour. ` +
      `(Ticket ${envelope.ticket_id})`);
    return [{ json: { done: event, provisioned: true } }];
  } catch (err) {
    await postToChannel(helpers, env('SLACK_ALERT_CHANNEL'),
      `:warning: Manual provisioning needed for ${envelope.tenant_id}: add ${envelope.requester_email} ` +
      `to ${envelope.item_display_name} (group ${group || 'n/a'}). Ticket ${envelope.ticket_id}. ` +
      `Reason: ${truncate(err.message, 300)}`);
    return [{ json: { done: event, provisioned: false, error: err.message } }];
  }
}

if (event === 'approval.denied') {
  const note = envelope.decision_note ? ` Note from your manager: "${truncate(envelope.decision_note, 500)}"` : '';
  await dm(helpers, envelope.requester_email,
    `Your request for ${envelope.item_display_name} wasn't approved.${note} ` +
    `Reply to the help desk if you have questions. (Ticket ${envelope.ticket_id})`);
  return [{ json: { done: event } }];
}

return [{ json: { ignored: event } }];
