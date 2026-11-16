// IT help desk free trial: the website's server side. Copy this file and
// ../lib/helpdesk-core.mjs into the site's netlify/ folders. See ../../README.md.
//
// Netlify environment variables (Site configuration -> Environment variables):
//   HELPDESK_AGENT_URL        https://<the agent's public hostname>
//   HELPDESK_SIGNING_SECRET   the agent's WEBCHAT_SIGNING_SECRET (exactly the same value)
//   HELPDESK_COOKIE_SECRET    a separate long random string (signs visitor and sign-in cookies)
//   HELPDESK_SITE_URL         https://valleyagentworks.com
import { originOk, rateLimit, sendEmail, emailShell, esc, store } from "../lib/common.mjs";
import { createHandler, PATHS } from "../lib/helpdesk-core.mjs";

export const config = { path: PATHS };

// A small per-key limiter on Netlify Blobs (the site's rateLimit helper is per IP).
async function rateLimitKey(key, max, windowSec) {
  const s = store("helpdesk-limits");
  const now = Date.now();
  const hits = ((await s.get(key, { type: "json" })) || []).filter((t) => now - t < windowSec * 1000);
  if (hits.length >= max) return false;
  hits.push(now);
  await s.setJSON(key, hits);
  return true;
}

export default createHandler({
  env: process.env,
  fetch: (...args) => fetch(...args),
  originOk,
  rateLimit,
  rateLimitKey,
  sendEmail,
  linkEmail: ({ link, lang }) => {
    const es = lang === "es";
    return {
      subject: es ? "Confirme su correo para seguir usando la mesa de ayuda" : "Confirm your email to keep using the IT help desk",
      text: (es ? "Abra este enlace en los próximos 20 minutos para terminar su registro:" : "Open this link within 20 minutes to finish signing up:") + `\n\n${link}`,
      html: emailShell(es ? "Confirme su correo" : "Confirm your email",
        `<p>${es ? "Abra este enlace en los próximos 20 minutos para terminar su registro." : "Open this link within 20 minutes to finish signing up."}</p>` +
        `<p><a href="${esc(link)}" style="color:#5fb3c9">${es ? "Confirmar mi correo" : "Confirm my email"}</a></p>` +
        `<p style="color:#a6b4ba">${es ? "Si usted no lo solicitó, ignore este mensaje." : "If you didn't ask for this, ignore this email."}</p>`),
    };
  },
  // onSignup: async ({ email, name, business, lang }) => { /* save the verified sign-up as a lead */ },
});
