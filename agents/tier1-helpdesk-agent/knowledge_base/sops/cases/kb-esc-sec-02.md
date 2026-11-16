---
doc_id: KB-ESC-SEC-02
title: Phishing — clicked / credentials entered / MFA approved
system_name: Security / Phishing
os_platform: any
last_verified_date: 2026-11-13
---

# Phishing — clicked / credentials entered / MFA approved

Area: Security / Phishing. Handling: Escalate-only. Give only the safe first steps below, collect the handoff packet (KB-POLICY-HANDOFF), and hand off to a person. Don't troubleshoot further.

**What users say:** “I clicked the link and typed my password” · “I approved a prompt I didn't expect”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Safe first steps:**
1. Stop. Don't click anything else; don't enter more information.
2. Change the password from a different, trusted device and turn on MFA — first aid only; the human must do the formal containment.
3. Note exactly what happened: link opened, file opened, credentials entered, MFA approved, information sent, payment initiated.

**If that doesn't fix it:** There is no “step 1 fails” here — this is already an incident.

**Escalate to a person when:** Escalate immediately as a security incident. A human resets the password and revokes all sessions (a reset alone leaves tokens valid), re-registers MFA, deletes mailbox rules / forwarding / app consents the attacker created, reviews sign-in logs and Sent items, warns recipients, and isolates / scans the machine if an attachment was opened. If bank, SSN, or card data was entered, also point to IdentityTheft.gov / the bank (FTC guidance).

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
