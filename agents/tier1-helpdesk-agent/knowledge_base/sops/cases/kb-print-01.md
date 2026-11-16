---
doc_id: KB-PRINT-01
title: Won't print / shows offline
system_name: Printers
os_platform: any
last_verified_date: 2026-11-13
---

# Won't print / shows offline

Area: Printers. Handling: Guide the user through the steps, then escalate if the escalation point is reached.

**What users say:** “The printer isn't printing” · “It says offline” · “Nothing came out”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Steps for the user, in order:**
1. Check the printer is on, has paper and toner, and what its panel says. Check the correct printer is selected — defaults change after updates.
2. Check the print queue — clear stuck jobs and restart the Print Spooler.
3. Ask: can anyone else print to it?

**If that doesn't fix it:** Remove and re-add the printer by IP address (not discovery); install the vendor driver, not the generic one; give a network printer a reserved / static IP.

**Escalate to a person when:** A shared or floor printer is down for everyone — that's an incident, even though it feels routine. Also escalate repeated jams after clearing (torn paper inside), or any driver / firmware work needing admin rights.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
