# Prompt for the website rebuild agent

Paste everything below the line into the Claude session that is rebuilding valleyagentworks.com.

---

Add a "Try the IT help desk" free trial to the site. The back end already exists; your job is
the website side.

**Read first:** in the `cameronksh4-byte/EACOS` repository, branch `ccr-0db7f14e-t7fp4r`, the
folder `agents/tier1-helpdesk-agent/integrations/website/`. `README.md` there is the contract.
Follow it exactly; do not redesign the flow.

**What to build**

1. Copy `netlify/lib/helpdesk-core.mjs` to this site's `netlify/lib/` unchanged, and
   `netlify/functions/api-helpdesk.mjs` to `netlify/functions/`. It uses this site's existing
   helpers in `netlify/lib/common.mjs` (`originOk`, `rateLimit`, `sendEmail`, `emailShell`,
   `esc`, `store`). Check each import still exists with that name and signature.
2. Create `/helpdesk/` (EN/ES like the rest of the site, in the current design system):
   - a short explanation: 5 free questions with no sign-up, 10 more after creating a free account,
     then a $20 credit pack (50 questions) or a monthly plan;
   - the chat. Start from `assets/helpdesk-widget.js`, restyle it, and translate its strings by
     setting `window.HELPDESK_TEXT` before the script loads. Keep its behaviour;
   - handle `?signup=done` (welcome back, you're signed in) and `?signup=expired` (ask again).
3. Create `/helpdesk/plans.html`: credit pack ($20 for 50 questions, never expire), Starter
   ($79/month) and Pro ($149/month), and "need it for your whole team? talk to us". Take the
   monthly question counts from the table in the contract README, and check with the owner
   before publishing them: they are placeholders. Buttons can be placeholders too: the payment
   provider hasn't been chosen. Do not invent prices or features beyond these.
4. In `onSignup` (commented out in `api-helpdesk.mjs`), save the verified sign-up as a lead the
   same way `api-lead.mjs` does, with the consent wording shown on the form.
5. Link the new page from the home page.

**Rules**

- The four `HELPDESK_*` values are Netlify environment variables. List their names for the
  owner; never put a value in the repo or in page source.
- Render every reply with `textContent`. Never `innerHTML`.
- The browser must not be able to send `account_email`, `visitor_id` or `client_ip`.
- Don't add analytics or logging that records what visitors type.
- Add the page to the privacy policy's list of what is collected (question text is sent to the
  help desk service to produce an answer; a cookie counts free questions; email only at sign-up).
  The policy still needs the attorney review already noted in this repo's README.
- Keep the existing manual test checklist style: add checks for 5 free questions then the
  sign-up form, the emailed link signing in, the counter showing 10 after sign-in, and the
  plans link appearing when those run out.

**Done means**: `npx netlify dev` serves `/helpdesk/`, a question returns an answer once the
four variables point at a running agent, the sixth anonymous question shows the sign-up form,
and the README lists the new variables and test steps. Report anything in the contract you
could not follow instead of working around it.
