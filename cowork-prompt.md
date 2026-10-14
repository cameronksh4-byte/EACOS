# Task: Build a lead-generation funnel on my Netlify website — Valley AgentWorks

## Who I am
Valley AgentWorks is an AI implementation business serving home-services contractors (HVAC, roofing, plumbing, electrical) in the Rio Grande Valley, Texas. Visitors arrive from Facebook groups, QR codes, SBDC events and press. They use a free tool or take a survey, see a scorecard, and book a free 20-minute **Missed-Call Audit Review**. Every lead must be stored, notified within 1 minute, and tracked through pipeline stages.

## What we already know (from an earlier session)
- The GitHub repo `cameronksh4-byte/eacos` does **not** contain the website (only Python tutorials and empty README folders). Don't build there.
- My Netlify site is open in my browser. Use it to find where the site's source lives.

## STEP 0 — Inspect first, then stop and wait for my OK
1. In my open Netlify tab, go to **Site configuration → Build & deploy → Continuous deployment**. Find the linked Git repo, build command, publish directory and Node version.
   - If it uses manual or drag-and-drop deploys (no repo), download the current deploy, put it in a new local folder, and initialize git.
2. Open the live site and record the framework, folder structure, brand colors (hex), fonts, logo file and existing pages.
3. Check my Netlify plan (free or Pro) and whether Forms, Functions and Blobs are enabled.
4. Report your findings and give me a plan in **under 25 lines**. **Do not change anything until I say OK.**

Rules: work on a new git branch. Never delete or restyle my existing pages without asking. Match my brand colors, fonts and logo. Use plain HTML/CSS/JS or my existing framework, with no heavy dependencies.

## Pages (mobile-first, fast, accessible, English/Spanish toggle on every page)
1. **/** — Headline "Stop losing jobs to voicemail". Short proof section. Trade selector (HVAC, Roofing, Plumbing, Electrical). Three cards linking to the tools. One primary CTA: "Book a free audit".
2. **/surveys** — Hub listing every survey and tool with a short description and time to complete: RGV Home Services Survey, AI Readiness Quiz, Missed-Call Revenue Calculator. Adding a new survey should only take one config entry.
3. **/calculator**
   - Inputs: trade (presets for average job value), calls per month, % missed, % of calls that are real leads, close rate.
   - Formula: revenue at risk = calls × missed% × lead% × close% × average job value. Show monthly and yearly figures, and show the math.
   - Show the headline number without a gate. Put the detailed email/PDF report behind name, email and phone, with consent checkboxes.
4. **/quiz** — AI Readiness Quiz with 8–10 questions covering lead handling, after-hours coverage, follow-up speed, estimates, reviews, tools and team comfort. Score 0–100 with three tiers, plus the top 3 quick wins per tier. The result page ends with the booking CTA.
5. **/survey/rgv-home-services** — 12–15 questions, one per screen, with a progress bar. Save answers as the respondent goes.
   - Questions: trade; employee count; years in business; how calls are answered today; after-hours coverage; % of calls missed; time to respond to web leads; AI tools in use; biggest admin time sinks; top concerns about AI; interest in training; share of customers who prefer Spanish; how estimates are followed up; optional contact opt-in for a free benchmark report and audit.
6. **/book** — "Missed-Call Audit Review" (20 minutes). Embed my scheduler (ask me: Cal.com, Calendly or Google appointment scheduling, plus the link).
   - Prefill name, email, phone and trade through URL parameters.
   - Settings: America/Chicago time zone, 15-minute buffer, no same-day bookings after 3 p.m., email confirmation, reminders 24 h and 1 h before.
   - If no embed is available, build a simple slot picker backed by a Netlify Function and Google Calendar.
7. **/thanks, /privacy, /terms, /sms-terms** — Plain-language templates I'll have an attorney review.

## Lead pipeline
- All forms post to a Netlify Function that validates input and blocks spam (honeypot plus rate limit).
- Storage: I'd suggest **Netlify Blobs** (built in, no extra account, the admin board can read and write it directly) with **Netlify Forms** as a backup inbox. Recommend otherwise if you see a better fit for my setup (Airtable, Notion, Google Sheets), and explain why.
- Lead fields: name, business, trade, phone, email, language, source page, UTM source/medium/campaign, tool results (calculator number, quiz score), consent flags with timestamp, IP and the exact consent text shown, pipeline stage, notes.
- Stages: New → Contacted → Audit booked → Audit done → Pilot offered → Pilot active → Package signed → Lost.
- **/admin** — Private and password protected.
  - On Netlify Pro, use Netlify's password protection. On the free plan, use an `ADMIN_PASSWORD` env var checked server-side with a signed cookie.
  - Simple kanban of the stages, lead detail view, filters by trade and source. I can drag or move leads between stages and add notes.
- Notifications within 1 minute of a new lead:
  - Email alert to me and a confirmation email to the lead, through **Resend**. The API key goes in an env var, never in code; don't ask me to paste it into chat.
  - SMS (suggest Twilio) goes **only** to leads who ticked the SMS consent box.

## Consent and compliance
- Separate **unchecked** checkboxes for email and SMS consent, with clear disclosure language, STOP/HELP wording, and links to /privacy and /sms-terms.
- Store the consent text shown with each submission.
- No marketing texts without SMS consent. The privacy policy explains in plain language what is collected and why.
- Show survey results only in aggregate unless a respondent opts in. Add a visible note on survey pages that responses are voluntary.

## Tracking
- Capture UTM parameters on every page and carry them through all forms.
- Privacy-friendly analytics (ask me: Plausible or GA4). Events: page_view, tool_start, tool_complete, lead_submit, booking_click, booking_complete.

## Quality
- Lighthouse 90+ on mobile, semantic HTML, keyboard accessible, alt text, no layout shift.
- **Cite sources for any statistic shown. Do not display a statistic you cannot source.**

## Ask me for these (after STEP 0, all at once)
Scheduler and booking link · Plausible or GA4 and its ID · Email address for lead alerts · Resend "from" address on a verified domain · SMS yes/no (Twilio) · Netlify plan · Logo and brand colors if they're not on the live site · Trade-specific photos (optional). For anything I leave open, use a sensible default and note it in the README.

## Deliverables
1. Working pages and functions on a new branch, plus an updated `netlify.toml`.
2. A list of required environment variables (names only, no values).
3. A README with setup steps and a manual test checklist.
4. A short list of anything still needed from me.
5. Run the site locally (`netlify dev`) and test every form, the calendar/booking flow and the admin board. Then summarize what you tested and what passed or failed.
6. Don't deploy to production or merge without my OK. A Netlify deploy preview from the branch is fine.

Start with STEP 0 now.
