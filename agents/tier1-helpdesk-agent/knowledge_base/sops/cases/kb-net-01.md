---
doc_id: KB-NET-01
title: No internet / no Wi-Fi
system_name: Wi-Fi / Network
os_platform: any
last_verified_date: 2026-11-13
---

# No internet / no Wi-Fi

Area: Wi-Fi / Network. Handling: Guide the user through the steps, then escalate if the escalation point is reached.

**What users say:** “The internet is down” · “I can't connect to Wi-Fi” · “Wi-Fi connected but no internet”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Steps for the user, in order:**
1. Scope first: one machine, or everyone nearby?
2. Check airplane mode and any physical Wi-Fi switch. Check the network actually joined — the guest network is often picked up by accident.
3. Try Ethernet in a nearby port.

**If that doesn't fix it:** Forget the network and rejoin, typing the password fresh. Release and renew the address (ipconfig /release then /renew on Windows) — a 169.254 address means DHCP isn't answering. Restart the network adapter.

**Escalate to a person when:** More than one user in the same area, nothing gets an address, or wired is also down — a network incident. DHCP scope, access point, ISP, and firewall are human / network-admin work.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
