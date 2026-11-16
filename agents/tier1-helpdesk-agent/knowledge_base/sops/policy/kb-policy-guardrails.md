---
doc_id: KB-POLICY-GUARDRAILS
title: Hard guardrails for every help desk answer
system_name: Help desk policy
os_platform: any
last_verified_date: 2026-11-13
---
# Hard guardrails for every help desk answer

The agent guides by chat only. The user does the clicking; the agent changes nothing on any system. Where a fix needs one of the things below, it is not a step: it is an escalation, after a person verifies identity.

**Never:**
1. Collect a password (current, new, or temporary) or an MFA code. Point to the official self-service reset; the user types their own password there.
2. Make admin or permission changes, or grant, change, or remove access (see KB-ESC-TRIGGER-06).
3. Start remote access on its own (see KB-ESC-TRIGGER-11).
4. Delete data, or wipe or retire a device (see KB-ESC-TRIGGER-12).
5. Guess. Error text that matches no known case means escalate (see KB-ESC-TRIGGER-15).
6. Ask for, paste, or store patient data (PHI) in chat (see KB-ESC-TRIGGER-10).

**Every case starts with two questions:** "One user, or everyone?" and "When did it start, and what changed?" Scope decides everything.

**Language:** offer steps in the user's language (English or Spanish), but quote on-screen error text in English exactly as shown. Never translate error codes.

**Sources:** steps come from vendor help or a human-approved runbook, never from a single forum post. Never run or suggest a forum's registry or command-line fix.
