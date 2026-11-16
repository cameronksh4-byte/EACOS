---
doc_id: KB-FILES-02
title: File missing / “I deleted it” / sync conflict
system_name: Shared Drives / Files
os_platform: any
last_verified_date: 2026-11-13
---

# File missing / “I deleted it” / sync conflict

Area: Shared Drives / Files. Handling: Guide the user through the steps, then escalate if the escalation point is reached.

**What users say:** “My file is gone” · “I can't find the document” · “It says conflicted copy”

**Ask first:** One user, or everyone? When did it start, and what changed?

**Steps for the user, in order:**
1. Where was it saved — local, network share, OneDrive / SharePoint / Drive?
2. Search by name and content; check Recent files in the app.
3. Check the Recycle Bin on the machine and the online recycle bin — OneDrive / SharePoint have a second, site-collection bin.
4. For a sync conflict, compare versions with the user before deleting anything — don't pick a version for them.

**If that doesn't fix it:** Use version history — an overwritten file comes back in seconds. For a share, Previous Versions / shadow copies. Pause and resume sync, or unlink / relink the sync client and let it re-index — do not delete the local folder.

**Escalate to a person when:** Any restore from backup (a human restores to a different location, so nothing current is overwritten), a genuinely local file with no backup (stop writing to that disk immediately), or a deletion that looks deliberate (HR / security matter).

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
