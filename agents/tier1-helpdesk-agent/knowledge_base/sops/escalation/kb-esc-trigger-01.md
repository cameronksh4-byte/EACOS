---
doc_id: KB-ESC-TRIGGER-01
title: Must escalate: Suspected malware / ransomware
system_name: Must escalate
os_platform: any
last_verified_date: 2026-11-13
---

# Must escalate: Suspected malware / ransomware

Handling: Escalate-only. The agent does not attempt a fix. It gives only the safe first aid below, collects the handoff packet (KB-POLICY-HANDOFF), and hands off immediately to the client's named human contact, with urgency.

**Triggers:** Files encrypted or renamed, a ransom note on screen, endpoint protection reports a detection, files changing by themselves, or more than one machine showing the same detection.

**Collect for the handoff:** Device name / asset tag, user, exact detection name + file path if shown, when behaviour changed, what was downloaded or opened, whether others are affected, a phone photo of any ransom note (don't copy note text with an address/link into chat).

**Safe first aid only:** Disconnect from the network (pull the Ethernet cable, turn Wi-Fi off). Leave the machine powered ON. Do not restart repeatedly, do not plug in a backup drive, do not pay or reply to the ransom demand, do not keep using the machine.

Shared baseline knowledge base: generic steps. A client's own SOP for the same issue wins. Never collect a password or MFA code; never make admin or permission changes.
