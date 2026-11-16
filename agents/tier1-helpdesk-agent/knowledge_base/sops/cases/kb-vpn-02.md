---
doc_id: KB-VPN-02
title: VPN connects, then drops
system_name: VPN
os_platform: any
last_verified_date: 2026-11-13
---

# VPN connects, then drops

Area: VPN. Handling: Guide the user through the steps, then escalate if the escalation point is reached.

**What users say:** “VPN keeps dropping every few minutes”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Steps for the user, in order:**
1. Reboot the home router first.
2. Test on a mobile hotspot — if it's stable there, the fault is the home Wi-Fi / ISP, not the VPN.

**If that doesn't fix it:** Pull the client logs and read the disconnect reason — an idle timeout is a policy setting, not a fault.

**Escalate to a person when:** Drops for several users at once, or the fix needs a concentrator / policy change.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
