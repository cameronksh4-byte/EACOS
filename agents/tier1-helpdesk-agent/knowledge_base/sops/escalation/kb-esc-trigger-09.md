---
doc_id: KB-ESC-TRIGGER-09
title: Must escalate: Backup / restore
system_name: Must escalate
os_platform: any
last_verified_date: 2026-11-13
---

# Must escalate: Backup / restore

Handling: Escalate-only. The agent does not attempt a fix. It gives only the safe first aid below, collects the handoff packet (KB-POLICY-HANDOFF), and hands off immediately to the client's named human contact, with urgency.

**Triggers:** Any restore request, a backup failure, or “is this backed up?” where the answer matters.

**Collect for the handoff:** What needs restoring, from when, where it lived, who owns the data.

**Safe first aid only:** Don't restore, don't delete, don't overwrite. A human restores to a different location and verifies with the data owner.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
