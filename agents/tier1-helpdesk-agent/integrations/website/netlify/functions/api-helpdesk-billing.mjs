// Stripe payments for the help desk free trial. Copy this file and
// ../lib/helpdesk-stripe.mjs (next to helpdesk-core.mjs) into the site. See ../../README.md.
//
// Netlify environment variables, in addition to the four HELPDESK_* ones:
//   STRIPE_SECRET_KEY       a restricted key: write Checkout Sessions and Customer portal,
//                           read Customers and Subscriptions. Nothing else.
//   STRIPE_WEBHOOK_SECRET   the signing secret (whsec_...) of the webhook endpoint
//   STRIPE_PRICE_CREDITS    price ID of the $20 credit pack (one-time)
//   STRIPE_PRICE_STARTER    price ID of the $79/month plan
//   STRIPE_PRICE_PRO        price ID of the $149/month plan
//   HELPDESK_CREDIT_PACK_SIZE   questions per pack (default 50)
import { originOk, rateLimit, alertOwner } from "../lib/common.mjs";
import { createBillingHandler, BILLING_PATHS } from "../lib/helpdesk-stripe.mjs";

export const config = { path: BILLING_PATHS };

export default createBillingHandler({
  env: process.env,
  fetch: (...args) => fetch(...args),
  originOk,
  rateLimit,
  alert: (subject, detail) => alertOwner(subject, [["Detail", detail]], subject),
});
