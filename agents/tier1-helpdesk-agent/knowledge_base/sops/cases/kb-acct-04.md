---
doc_id: KB-ACCT-04
title: SSO login loop
system_name: Passwords / Accounts
os_platform: any
last_verified_date: 2026-11-13
---

# SSO login loop

Area: Passwords / Accounts. Handling: Guide the user through the steps, then escalate if the escalation point is reached.

**What users say:** “It keeps sending me back to the login page”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Steps for the user, in order:**
1. Scope: one user, or everyone on that app?
2. Clear cookies for both the app and the identity-provider domain; try incognito or a different browser.

**If that doesn't fix it:** A human checks signing-certificate expiry, the attribute the app expects (email / UPN) against the directory record, and sign-in logs.

**Escalate to a person when:** The loop hits everyone on an application — configuration / certificate failure. Identity owner, not Tier 1.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
