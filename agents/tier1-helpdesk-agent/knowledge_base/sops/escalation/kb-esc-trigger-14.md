---
doc_id: KB-ESC-TRIGGER-14
title: Must escalate: Vendor / tenant-wide outage
system_name: Must escalate
os_platform: any
last_verified_date: 2026-11-13
---

# Must escalate: Vendor / tenant-wide outage

Handling: Escalate-only. The agent does not attempt a fix. It gives only the safe first aid below, collects the handoff packet (KB-POLICY-HANDOFF), and hands off immediately to the client's named human contact, with urgency.

**Triggers:** A vendor status page confirms an outage, or the same failure hits everyone — mail flow, a SaaS app, the identity provider.

**Collect for the handoff:** Vendor / app name, status-page wording, how many users, when it started, any error text.

**Safe first aid only:** Tell users it's a known outage, don't reinstall or reconfigure anything, and wait for the vendor / human update. A human owns the vendor ticket and user communications.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
