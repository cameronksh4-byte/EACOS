// Code node: verify a webhook from the help desk agent and pass the event on.
// Output: one item { ok, status, event, envelope }. The Respond node returns `status`,
// and the handler node does nothing unless ok is true.
const helpers = this.helpers;
const item = $input.first();
const raw = await rawBodyOf(helpers, item);
const ok = verifyAgentSignature(env('N8N_AGENT_SIGNING_SECRET'), raw, header(item, 'x-agent-signature'));
let envelope = null;
if (ok) {
  try {
    envelope = JSON.parse(raw);
  } catch (e) {
    return [{ json: { ok: false, status: 400, error: 'invalid json' } }];
  }
}
return [{ json: { ok, status: ok ? 200 : 401, event: envelope ? envelope.event : null, envelope } }];
