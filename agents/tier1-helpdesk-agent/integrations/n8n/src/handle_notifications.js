// Code node: post agent notifications to Slack.
//   outage.detected, thread.escalated -> SLACK_ALERT_CHANNEL
//   sop_draft.created                 -> SLACK_SOP_REVIEW_CHANNEL with [Ingest] / [Reject]
//   report.weekly                     -> SLACK_EXEC_CHANNEL
const helpers = this.helpers;
const { ok, envelope } = $input.first().json;
if (!ok || !envelope) return [];
const event = envelope.event;
const data = envelope.data || {};

if (event === 'outage.detected' || event === 'thread.escalated') {
  await postToChannel(helpers, env('SLACK_ALERT_CHANNEL'), truncate(envelope.text, 3500));
  return [{ json: { done: event } }];
}

if (event === 'sop_draft.created') {
  const value = (decision) => JSON.stringify({ k: 'sop', t: envelope.tenant_id, d: data.draft_id, x: decision });
  const blocks = [
    {
      type: 'section',
      text: {
        type: 'mrkdwn',
        text: `*New SOP draft for review:* ${data.title}\n` +
          `\`${data.doc_id}\` · from ticket ${data.source_ticket_id} · tenant ${envelope.tenant_id}\n` +
          `Redacted: ${(data.redactions || []).join(', ') || 'nothing'}`,
      },
    },
    { type: 'section', text: { type: 'mrkdwn', text: '```' + truncate(data.markdown, 2800) + '```' } },
    {
      type: 'actions',
      block_id: `sop:${data.draft_id}`,
      elements: [
        {
          type: 'button', action_id: 'sop_ingest', style: 'primary',
          text: { type: 'plain_text', text: 'Ingest into knowledge base' }, value: value('approve'),
        },
        { type: 'button', action_id: 'sop_reject', text: { type: 'plain_text', text: 'Reject' }, value: value('reject') },
      ],
    },
  ];
  await postToChannel(helpers, env('SLACK_SOP_REVIEW_CHANNEL'), `New SOP draft: ${data.title}`, blocks);
  return [{ json: { done: event, draft_id: data.draft_id } }];
}

if (event === 'report.weekly') {
  // Slack mrkdwn doesn't render Markdown tables, so send the report preformatted.
  const blocks = [
    { type: 'header', text: { type: 'plain_text', text: truncate(`Weekly help desk report: ${envelope.tenant_id}`, 150) } },
    { type: 'section', text: { type: 'mrkdwn', text: '```' + truncate(data.markdown || envelope.text, 2900) + '```' } },
  ];
  await postToChannel(helpers, env('SLACK_EXEC_CHANNEL'), `Weekly help desk report: ${envelope.tenant_id}`, blocks);
  return [{ json: { done: event } }];
}

return [{ json: { ignored: event } }];
