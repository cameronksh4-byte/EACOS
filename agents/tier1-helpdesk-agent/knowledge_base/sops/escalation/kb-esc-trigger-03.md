---
doc_id: KB-ESC-TRIGGER-03
title: Must escalate: Account compromise
system_name: Must escalate
os_platform: any
last_verified_date: 2026-11-13
---

# Must escalate: Account compromise

Handling: Escalate-only. The agent does not attempt a fix. It gives only the safe first aid below, collects the handoff packet (KB-POLICY-HANDOFF), and hands off immediately to the client's named human contact, with urgency.

**Triggers:** Sign-in from an unfamiliar place, mail the user didn't send, rules or forwarding the user didn't create, a password changed without the user doing it, MFA prompts the user didn't trigger.

**Collect for the handoff:** User, when the odd sign-in or mail was noticed, any unfamiliar locations / devices, whether credentials were entered on a fake page, whether an MFA prompt was approved, who received mail from the account.

**Safe first aid only:** Don't use the account further. A human blocks sign-in and revokes ALL sessions, resets the password, re-registers MFA, deletes rules / forwarding / app consents, reviews logs, and warns recipients. Escalate from the point it's suspected — not from the point it's confirmed.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
