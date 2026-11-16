---
doc_id: KB-ESC-TRIGGER-13
title: Must escalate: System changes needing admin rights
system_name: Must escalate
os_platform: any
last_verified_date: 2026-11-13
---

# Must escalate: System changes needing admin rights

Handling: Escalate-only. The agent does not attempt a fix. It gives only the safe first aid below, collects the handoff packet (KB-POLICY-HANDOFF), and hands off immediately to the client's named human contact, with urgency.

**Triggers:** An install, reinstall, or update requiring local admin; driver or firmware updates; OS reinstall / reimage; certificate changes.

**Collect for the handoff:** App name and version, exact error code, device, whether others are affected.

**Safe first aid only:** Guide only up to the admin boundary — e.g. “install from the company portal.” A human / admin performs the change.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
