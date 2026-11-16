---
doc_id: KB-ACCT-03
title: MFA / 2FA fails
system_name: Passwords / Accounts
os_platform: any
last_verified_date: 2026-11-13
---

# MFA / 2FA fails

Area: Passwords / Accounts. Handling: Guide the user through the steps, then escalate if the escalation point is reached.

**What users say:** “The code doesn't work” · “The push never arrives” · “I lost my phone”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Steps for the user, in order:**
1. Check automatic date/time is on — clock drift rejects time-based codes.
2. Check notifications are allowed for the authenticator app.
3. Try a backup method before unenrolling anything.

**If that doesn't fix it:** Force a time sync and retry; switch from Wi-Fi to mobile data for a missing push; reinstall the authenticator app.

**Escalate to a person when:** Any MFA reset, re-enrolment, lost device, or temporary bypass. MFA resets are exactly what social engineering targets — identity proof must be stronger than a chat confirmation, and a human must do it. Also escalate if MFA fails for a group (identity-provider problem).

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
