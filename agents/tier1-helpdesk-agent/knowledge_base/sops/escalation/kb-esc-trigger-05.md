---
doc_id: KB-ESC-TRIGGER-05
title: Must escalate: Network outage affecting everyone
system_name: Must escalate
os_platform: any
last_verified_date: 2026-11-13
---

# Must escalate: Network outage affecting everyone

Handling: Escalate-only. The agent does not attempt a fix. It gives only the safe first aid below, collects the handoff packet (KB-POLICY-HANDOFF), and hands off immediately to the client's named human contact, with urgency.

**Triggers:** Whole office, floor, or site without internet or Wi-Fi; no one can reach shared drives or printers; VPN down for several remote users; DNS failing broadly.

**Collect for the handoff:** How many people / which area, when it started, whether wired AND wireless are affected, any ISP / router lights or messages, whether the office router or access point was touched.

**Safe first aid only:** Tell users not to keep changing their own network settings — it creates extra work for the human. A human checks ISP, router / firewall, DHCP, DNS, and access points, and posts ONE update to users instead of answering the same ticket forty times.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
