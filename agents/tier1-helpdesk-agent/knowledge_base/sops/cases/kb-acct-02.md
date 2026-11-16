---
doc_id: KB-ACCT-02
title: Account locked out
system_name: Passwords / Accounts
os_platform: any
last_verified_date: 2026-11-13
---

# Account locked out

Area: Passwords / Accounts. Handling: Guide the user through the steps, then escalate if the escalation point is reached.

**What users say:** “I'm locked out” · “It says my account is locked”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Steps for the user, in order:**
1. Confirm whether it's a lockout or an expired password — they look identical to the user.
2. Ask whether they changed the password recently on one device only; clear the saved credential in Windows Credential Manager / Keychain on that device.

**If that doesn't fix it:** If it locks again within the hour, another device or service is still replaying the old password.

**Escalate to a person when:** A human unlocks, after identity proof. Immediate escalation if several accounts lock at the same time — possible brute-force / security incident.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
