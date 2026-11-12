// Code node (after a Schedule trigger): ask the agent to build and deliver each
// tenant's weekly executive report. AGENT_TENANTS is a comma-separated list.
const helpers = this.helpers;
const base = env('AGENT_BASE_URL').replace(/\/+$/, '');
const tenants = env('AGENT_TENANTS').split(',').map((t) => t.trim()).filter(Boolean);
const results = [];
for (const tenant of tenants) {
  const res = await helpers.httpRequest({
    method: 'POST',
    url: `${base}/v1/reports/weekly`,
    qs: { tenant_id: tenant, deliver: 'true' },
    headers: { 'x-webhook-secret': env('AGENT_WEBHOOK_SECRET') },
    returnFullResponse: true,
    ignoreHttpStatusErrors: true,
    json: true,
  });
  results.push({ json: { tenant, status: res.statusCode, delivered: res.body && res.body.delivered } });
}
return results;
