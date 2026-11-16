---
doc_id: KB-ESC-FILES-01
title: Can't access a shared drive — “Access denied”
system_name: Shared Drives / Files
os_platform: any
last_verified_date: 2026-11-13
---

# Can't access a shared drive — “Access denied”

Area: Shared Drives / Files. Handling: Escalate-only. Give only the safe first steps below, collect the handoff packet (KB-POLICY-HANDOFF), and hand off to a person. Don't troubleshoot further.

**What users say:** “I can't open the shared drive” · “Access denied”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Safe first steps:**
1. Scope: can a colleague in the same role open it? That separates a user problem from a broken permission.
2. Sign out and back in — group changes need a fresh token.
3. Check whether the user is on VPN if the share needs it; check the exact folder path / mapped-drive letter.

**If that doesn't fix it:** Diagnose only. A human confirms group membership and who owns the resource, adds the user to the group (not a direct folder grant), and confirms access with the user.

**Escalate to a person when:** Always escalate the permission change itself — especially for sensitive data (finance, HR, patient files) or any exception to policy. Approval comes from the data owner, not the help desk. The AI never grants access.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
