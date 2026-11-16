---
doc_id: KB-EMAIL-01
title: Can't send or receive email
system_name: Email / Outlook
os_platform: any
last_verified_date: 2026-11-13
---

# Can't send or receive email

Area: Email / Outlook. Handling: Guide the user through the steps, then escalate if the escalation point is reached.

**What users say:** “My email won't send” · “I'm not getting emails” · messages stuck in Outbox

**Ask first:** One user, or everyone? When did it start, and what changed?

**Steps for the user, in order:**
1. Check the internet — open a website.
2. In Outlook, use Sync / Send-Receive to force a manual sync.
3. Check the Outbox: a message with an attachment over 20 MB blocks everything behind it — remove the attachment or send it as a link.
4. Check Junk / Other / Focused tabs, rules, and quarantine.
5. Try Outlook on the web — if webmail works, it's a client problem, not a mailbox problem.

**If that doesn't fix it:** If webmail also fails, or there's a bounce, read the bounce code: 550 = permanent (bad address / blocked / policy); 421 / 451 = temporary, mail retries itself. Don't keep re-sending.

**Escalate to a person when:** Several users can't send, mail flow stops, the same domain always bounces, or forwarding/rules appeared that the user didn't create — a compromise sign. A human runs a message trace and checks SPF / DKIM / DMARC and the restricted-senders list.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
