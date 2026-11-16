---
doc_id: KB-ESC-TRIGGER-12
title: Must escalate: Data deletion / wiping
system_name: Must escalate
os_platform: any
last_verified_date: 2026-11-13
---

# Must escalate: Data deletion / wiping

Handling: Escalate-only. The agent does not attempt a fix. It gives only the safe first aid below, collects the handoff packet (KB-POLICY-HANDOFF), and hands off immediately to the client's named human contact, with urgency.

**Triggers:** Any request to delete files, mailboxes, or accounts, or to wipe / retire a device.

**Collect for the handoff:** What exactly, why, who approved it, whether a backup or transfer is needed first.

**Safe first aid only:** The AI never deletes anything. A human confirms approval and backup / transfer, then performs it.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
