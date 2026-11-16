// Prints the exact request the website's server would send to the agent, as JSON:
//   node sign_request.mjs <secret> '<json payload>'   ->   {"body": "...", "signature": "t=...,v1=..."}
// Used by tests/test_webchat.py to prove the website and the agent agree on the signature.
import { signAgent } from "../netlify/lib/helpdesk-core.mjs";

const [secret, payload] = process.argv.slice(2);
const body = JSON.stringify(JSON.parse(payload));
process.stdout.write(JSON.stringify({ body, signature: signAgent(secret, body) }));
