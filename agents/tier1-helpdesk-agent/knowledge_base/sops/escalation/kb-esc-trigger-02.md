---
doc_id: KB-ESC-TRIGGER-02
title: Must escalate: Data loss / deletion with no backup
system_name: Must escalate
os_platform: any
last_verified_date: 2026-11-13
---

# Must escalate: Data loss / deletion with no backup

Handling: Escalate-only. The agent does not attempt a fix. It gives only the safe first aid below, collects the handoff packet (KB-POLICY-HANDOFF), and hands off immediately to the client's named human contact, with urgency.

**Triggers:** File or folder missing and not in either Recycle Bin or version history; a local-only file with no backup; a whole shared folder or mailbox missing.

**Collect for the handoff:** File / folder name, where it was saved (local / share / OneDrive / SharePoint), when it was last seen, who else uses it, whether anyone else deleted or moved it.

**Safe first aid only:** Stop writing to that disk immediately — continued use overwrites what could be recovered. Don't run a recovery tool; don't restore anything yourself. A human restores from backup to a different location and confirms the version with the user.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
