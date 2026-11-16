---
doc_id: KB-VPN-01
title: VPN won't connect
system_name: VPN
os_platform: any
last_verified_date: 2026-11-13
---

# VPN won't connect

Area: VPN. Handling: Guide the user through the steps, then escalate if the escalation point is reached.

**What users say:** “VPN won't connect” · “It says authentication failed”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Steps for the user, in order:**
1. Distinguish: never connects, vs connects then drops.
2. Check the VPN client version; check whether an MFA prompt is appearing.
3. Check the internet first — VPN can't work without it.

**If that doesn't fix it:** Update the VPN client (clean uninstall, not install-over). Check the certificate / profile hasn't expired. Try a different gateway or region if one exists.

**Escalate to a person when:** Credentials or MFA can't be verified, several remote users drop at the same time (concentrator), or a certificate / gateway change is needed — human / network admin.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
