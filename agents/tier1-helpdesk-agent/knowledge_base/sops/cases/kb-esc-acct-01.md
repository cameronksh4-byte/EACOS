---
doc_id: KB-ESC-ACCT-01
title: Forgot or expired password
system_name: Passwords / Accounts
os_platform: any
last_verified_date: 2026-11-13
---

# Forgot or expired password

Area: Passwords / Accounts. Handling: Escalate-only. Give only the safe first steps below, collect the handoff packet (KB-POLICY-HANDOFF), and hand off to a person. Don't troubleshoot further.

**What users say:** “I forgot my password” · “It says my password expired”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Safe first steps:**
1. Check Caps Lock, keyboard language, and that the username is right.
2. Guide the user to the official self-service reset (Microsoft / Google / company page). The user types their own new password there — the agent never sees, asks for, or types it.
3. After a change, update the password on the phone and any mapped drive — old saved passwords replaying cause repeat lockouts.

**If that doesn't fix it:** Self-service fails, or the user can't complete identity proof in the self-service flow.

**Escalate to a person when:** Always escalate the reset itself to a human if self-service can't do it. The human verifies identity by policy (callback to a known number / manager confirmation), resets in the directory (Entra ID / AD / Google Workspace), sets change-at-next-login, and sends any temporary credential through a different channel. The AI never resets and never handles a temporary password.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
