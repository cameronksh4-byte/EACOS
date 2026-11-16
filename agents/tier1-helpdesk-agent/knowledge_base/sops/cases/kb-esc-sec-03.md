---
doc_id: KB-ESC-SEC-03
title: Possible malware / pop-ups / fake “infected” warning
system_name: Security / Phishing
os_platform: any
last_verified_date: 2026-11-13
---

# Possible malware / pop-ups / fake “infected” warning

Area: Security / Phishing. Handling: Escalate-only. Give only the safe first steps below, collect the handoff packet (KB-POLICY-HANDOFF), and hand off to a person. Don't troubleshoot further.

**What users say:** “My computer is acting weird” · pop-ups · programs opening by themselves

**Ask first:** One user, or everyone? When did it start, and what changed?

**Safe first steps:**
1. Don't click any pop-up; don't download a “cleaner” from the warning.
2. Ask what the endpoint protection actually reported (the detection name) and when the behaviour changed / what was downloaded.
3. Tell the user to stop working on the machine.

**If that doesn't fix it:** No further user steps — this is an escalation, not a guided fix.

**Escalate to a person when:** Escalate immediately. A human disconnects it from the network first, leaves it powered on (preserves memory / forensics), runs a full scan from the management console, checks persistence, resets credentials, and reimages if it's ransomware, a rootkit, or uncertain.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
