---
doc_id: KB-ESC-PERF-02
title: Blue / black screen (BSOD)
system_name: Slow Computers
os_platform: any
last_verified_date: 2026-11-13
---

# Blue / black screen (BSOD)

Area: Slow Computers. Handling: Escalate-only. Give only the safe first steps below, collect the handoff packet (KB-POLICY-HANDOFF), and hand off to a person. Don't troubleshoot further.

**What users say:** “My computer crashed to a blue / black screen”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Safe first steps:**
1. Calm the user. Note the exact stop code and any faulting driver name shown.
2. Note what changed recently — driver, update, new hardware or dock — and whether it crashes at boot, at wake, or under load.
3. Offer a temporary machine if one is available (a human arranges it).

**If that doesn't fix it:** There is no chat fix. A human boots to Safe Mode / WinRE, rolls back the driver or update, runs Windows Memory Diagnostic / chkdsk, and reads the minidump.

**Escalate to a person when:** Escalate immediately. Especially if the same stop code appears on several machines after a patch cycle — stop the rollout.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
