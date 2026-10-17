# 3. The 14-Day Zero-IT Shadow Pilot: Onboarding Blueprint

The pilot is a **sales instrument**. Its only job is to produce a **Caught Errors Ledger** the Owner/LCB believes, in numbers they recognize, on Day 14. Everything below serves that.

```
Day 0         Days 1–3            Day 4–6      Day 7          Days 8–13       Day 14            Day 15
Setup  ──►  SILENT SHADOW  ──►  LIVE ALERTS ──► CHECK-IN ──► LIVE ALERTS ──► ROI REVEAL ──►  Paid start
10 min      ledger only;         S1/S2 to      (15 min,      Day 10: book    & CLOSE          or graceful
            analyst verifies     ops manager   in person)    Owner for D14   (30 min)         exit
```

---

## 3.1 Before you ever walk in (done once, in Week 1)

Setup is only "10 minutes" if the hard work happens before the visit.

- **10 pre-provisioned pilot kits**, one envelope per prospect, containing:
  - A **QR card** that opens a WhatsApp chat with our Business number, pre-filled with `ALTA [KIT-CODE]`. Scanning it registers that phone to the kit's tenant.
  - The tenant's **unique intake email address**, e.g. `kit07@intake.[domain].com`. Rename it to the customer's name after setup.
  - 4 **laminated desk cards** (EN/ES): "How to send documents to CruceCheck" in 3 steps.
  - A printed **pilot agreement** with the confidentiality/service-provider addendum, plus the **compliance memo**.
- The tenant is pre-configured in **silent-shadow mode** (Days 1–3) with **S1/S2-only alerts** from Day 4.
- A tablet or phone with **e-signature** ready.

### What the pilot agreement says (1 page, plain language, counsel-reviewed)

1. **Dates:** the start date (Day 0) and the end date (Day 14).
2. **What we do:** receive forwarded documents, compare them, and report discrepancies. **We never file, transmit to CBP, or sign anything.**
3. **Confidentiality and service provider:** we process the broker's documents **only** to perform the checks. No other use, no sharing, no sale, no model training. U.S.-only hosting. Return or deletion on request at the end.
4. **The catch definition** (below), agreed and initialed.
5. **The threshold:** if fewer than **3 confirmed catches** by Day 14, they owe nothing and we delete or return their data at their choice. If **3 or more**, the Founding Partner plan begins on Day 15 at [$1,250]/month, month-to-month, cancel with 30 days' notice.
6. **The Broker-Neutral Pledge:** no brokerage license, no forwarding, no contacting or soliciting their clients. Ever.
7. **Names:** who signs (Owner/LCB), who champions (Ops Manager), and who gets alerts.

### The catch definition (agree on it on Day 0, so Day 14 has no arguments)

A **confirmed catch** is a discrepancy between two or more documents for the **same shipment** that:
- **(a)** the customer's clerk or ops manager **confirms was real**, and
- **(b)** was **not already corrected** in the version that would have been used to file, and
- **(c)** required, or would have required, a **resend, a correction, a call to the shipper/carrier/agencia, or a delay.**

**Not counted:** our own misreads (logged as false alarms), duplicates of the same error on the same load, and known, accepted differences (e.g., "this client always rounds to the nearest 10 kg").

**Severity**, confirmed by the customer:

| Level | Meaning | Counts toward 3? | Alerted live? |
|---|---|---|---|
| **S1: Critical** | Could have caused a hold, a turned-back truck, a rejected entry, or a post-entry correction | ✅ | ✅ immediately (from Day 4) |
| **S2: Material** | Needed a resend or correction before filing, or caused a delay | ✅ | ✅ (from Day 4) |
| **S3: Minor** | A rounding or cosmetic difference within tolerance | ❌ | ❌ ledger only |

---

## 3.2 The 10-Minute Zero-IT Setup

Do this **at their desk, with the person who actually receives documents** (the lead clerk or the ops manager). Pick their quietest hour. Use a timer, and say out loud, "This takes ten minutes," because keeping that promise is the first proof.

### Minute 0–2: Sign
- The Owner/LCB (or Ops Manager, if authorized) e-signs the pilot agreement on the tablet.
- Initial the **catch definition** together. Read it aloud in one sentence: *"We only count it if your team says it was real."*

### Minute 2–4: WhatsApp intake
1. Each clerk who receives documents scans the **QR card**. WhatsApp opens with `ALTA [KIT-CODE]` pre-filled, they hit send, and their phone is registered. That's about 30 seconds per person.
2. They **save the contact** as "CruceCheck ✅" so forwarding is easy to find.
3. **Test:** the clerk forwards one real document they received today. The bot replies with "✅ Recibido / Received" and, within about 12 seconds, a match or no-match result.

> **How documents flow:** when a factura, Carta Porte or BOL arrives in their usual WhatsApp chat (from the agencia, carrier or shipper), the clerk long-presses it, taps **Forward**, and picks **CruceCheck**. Several files can be forwarded at once. **Ask senders to include the CFDI XML** whenever possible, because it gives exact fields.
>
> **On groups:** add our number to a WhatsApp group only if your engineering lead has confirmed an official Meta pathway for it. Otherwise the 1:1 forward is the method. Don't improvise.

### Minute 4–6: Email intake
Use the decision tree, and never fight their IT setup during a pilot.

```
What email do they use for documents?
│
├─ Gmail / Google Workspace ──► Settings → Forwarding → add kit address → confirm code
│                                 → Filter: has:attachment (factura OR "carta porte" OR BOL OR CFDI)
│                                 → Forward to kit address. DONE.
│
├─ Microsoft 365 / Outlook ──► Try: Rules → "Forward to" kit address on the intake mailbox.
│     │                          Send a test email with an attachment.
│     ├─ Test arrives ──────────► DONE.
│     └─ Bounce "550 5.7.520 … does not allow external forwarding" (default M365 policy)
│           ──► DON'T open an IT ticket. Pick one:
│               A) Clerk forwards manually (select the batch → Forward). Fine for 14 days.
│               B) Ask their top 2–3 senders (agencia, carrier) to CC the kit address.
│               C) WhatsApp-only pilot. Add email after Day 14.
│
└─ Other (GoDaddy, cPanel, Yahoo) ──► Usually allows a forward rule. Test it. Otherwise use A, B or C.
```

### Minute 6–7: Alert routing
- **Alert recipients:** the Ops Manager's WhatsApp, plus the **yard/dispatcher** if they want it. Many will want yard alerts only after Day 7, which is fine.
- **Language:** Spanish, English, or both.
- **Quiet hours:** e.g., no non-critical alerts after 9 p.m.
- Confirm out loud: *"Days 1 to 3, it stays silent. It just logs. Day 4, it starts alerting on the serious ones, and only to [Ops Manager]."*

### Minute 7–10: The live test and the baseline
1. **The "wow" test.** Ask: *"Do you remember a load from the last couple of weeks that had a paperwork problem? Do you still have the documents?"* Forward them now. When the bot catches the problem they already know about, the pilot is sold internally.
2. **Baseline questions** (write their answers on the ledger's Baseline tab, in their words):
   - Entries per day (normal / heavy)
   - Share of documents arriving by WhatsApp vs. email
   - Paperwork problems last month, as a rough count of resends, corrections, holds and trucks turned back
   - **What one bad load costs them:** detention, re-dray, staff hours, client credits. *Use their number, never ours.*
   - Hours per day the clerks spend cross-checking documents
3. **Leave a laminated desk card** at every clerk's station.
4. **Book Day 7 and Day 14 before you leave**, with the Owner/LCB on the Day 14 invite: *"On Day 14 I'll bring the full list. I'd like [Owner] there for 30 minutes since it's their decision."*

**What we never ask for in setup:** admin passwords, software installs, ABI or CargoWise access, VPN, or their IT person. If any of these come up, the answer is "not needed for the pilot."

---

## 3.3 Running the pilot (internal playbook)

| When | Who | What |
|---|---|---|
| **Daily** | Pilot analyst | Review every flag **within 1 business hour**. Mark each one *Likely real*, *Our misread* or *Duplicate*. A misread that would have reached the yard is a **P1 internal bug**. |
| **Day 1, 9:30 a.m.** | Rep (WhatsApp) | "Good morning [Name], it's working ✅ [N] documents arrived yesterday from [N] shipments. Does that sound like a normal share of your volume?" **If intake is under 50% of their stated volume, fix the flow the same day.** |
| **Day 3** | Rep (in person or a 5-min call) | Walk through the silent-shadow flags with the clerk and have her confirm or deny each one. This is how catches become *confirmed*. Then turn on S1/S2 live alerts to the Ops Manager. |
| **Days 4–13** | System and analyst | Live S1/S2 alerts. The analyst keeps the ledger current. Nothing reaches the yard unless the customer opted in. |
| **Day 7** | Rep (in person, 15 min) | **Mid-pilot check-in** (script below) |
| **Day 10** | Rep (WhatsApp) | Send a ledger snapshot and confirm the Day 14 meeting with the Owner/LCB |
| **Day 13** | Analyst and founder | Finalize the ledger and ROI summary. Print 2 copies. |
| **Day 14** | Founder and rep (in person, 30 min) | **ROI Reveal & Close** (script below) |

---

## 3.4 Day 7: Mid-Pilot Check-In script

**Who:** the Ops Manager (champion) plus the lead clerk (user). **Where:** their office, in a prime window. **Length:** 15 minutes. **Bring:** a ledger printout and pan dulce if you like.

**Goals:** (1) confirm intake coverage, (2) get catches confirmed, (3) fix anything annoying, (4) **lock the Day 14 meeting with the Owner/LCB.**

> **1. Open (1 min)**
> "Thanks, I'll keep it to 15 minutes. We're at the halfway point, so I want to show you what we've seen, hear what's been annoying, and plan the last week."
>
> **2. Coverage (2 min)**
> "Here's what's come through: **[N] shipments, [N] documents.** By what you told me on Day 0, that's about **[X]% of your volume.** Does that sound right?"
> *If it's under 70%:* "Which flows aren't coming through? Is it a sender, an inbox, or a shift?" Fix it today.
>
> **3. The catches (6 min)**
> "We've flagged **[N]** so far. **[Clerk]**, you confirmed **[N]** as real. Let me show the best one."
> *[Show the side-by-side.]*
> "What would have happened if this one had gone through?"
> *[Stop talking. Let them describe the cost and write it down word for word. This sentence goes on the Day 14 ROI page.]*
> "And **[Clerk]** fixed it at [time], [X hours] before the truck crossed. Good catch."
>
> **4. The honest part (3 min)**
> "Now tell me what's been annoying. Too many messages? The wrong person getting them? Anything we flagged that wasn't a real problem?"
> *Tune on the spot: recipients, quiet hours, severity, language. Thank them for every complaint, because complaints mean they're using it.*
>
> **5. Where we stand (1 min)**
> *If ≥ 3 confirmed:* "We've already passed the three we promised. This last week is about showing **[Owner]** the full picture."
> *If < 3:* "We're at **[N] of 3**, with a week left. Volume usually picks up [day], and I want to make sure we're seeing the email side too. Can we add [sender/inbox]?"
>
> **6. Lock Day 14 (2 min)**
> "On Day 14 I'll bring the full ledger and what it adds up to in your numbers. I'd like **[Owner/LCB]** there for 30 minutes, since they'd be the one deciding. Does **[date] at 10:30** work? I'll send the invite to you both now."

**Español (key lines):**
> "Vamos a la mitad. Han llegado **[N] embarques y [N] documentos**, más o menos el **[X]%** de su volumen. ¿Le suena correcto?"
> "Llevamos **[N]** alertas y **[Clerk]** confirmó **[N]** como reales. Le enseño la mejor. ¿Qué hubiera pasado si esta se hubiera ido así?"
> "Ahora lo honesto: ¿qué les ha molestado? ¿Muchos mensajes? ¿Le llegan a la persona equivocada?"
> "El día 14 traigo el registro completo. Me gustaría que estuviera **[Dueño]** 30 minutos, ya que es quien decide. ¿El **[fecha] a las 10:30**?"

**Red flags at Day 7, and what to do:**

| Red flag | Action |
|---|---|
| The Owner won't commit to Day 14 | Ask the Ops Manager: "What would [Owner] need to see to make time for this?" Offer 15 minutes instead of 30. |
| Intake under 50% | Re-do the email or WhatsApp setup today. Offer to extend the pilot by the days lost, **in writing**. |
| A false alarm upset the yard | Apologize, turn off yard alerts, and show the fix. Never argue about it. |
| Zero confirmed catches | Ask whether their volume or doc mix differs from what we expected. Be honest that it may not be a fit. |

---

## 3.5 Day 14: ROI Reveal & Close Meeting

**Who:** the Owner/LCB (**required**), the Ops Manager, and the lead clerk. From our side, the founder and the rep. **Length:** 30 minutes. **Bring:** 2 printed copies of the **Caught Errors Ledger** and the **one-page ROI Summary**, plus the Founding Partner agreement.

### What the ROI Summary page shows (all in *their* numbers)

```
CruceCheck: 14-Day Shadow Pilot Results · [Brokerage] · [Start] – [End]

Shipments checked ............ [612]       Documents checked ...... [1,980]
Flags raised ................. [11]        Confirmed by your team . [9]   (S1: 3 · S2: 6)
False alarms (our misses) .... [2]         Flag precision ......... [82%]
Median check time ............ [11 s]      Earliest catch ......... [5 h 10 m] before crossing

COST AVOIDED: using the costs YOUR team gave us on Day 0
  S1 catches  [3] × [$650]  "turned-back truck: detention + re-dray + 3 staff hours"  = [$1,950]
  S2 catches  [6] × [$120]  "resend + 1 staff hour"                                     = [$  720]
  ───────────────────────────────────────────────────────────────────────────────────────────────
  14-day total ......................................................................  [$2,670]
  Monthly equivalent (× 2.17) .......................................................  [$5,790]

CLERK TIME: [~40] min/day of manual cross-checking now pre-sorted (your estimate: [Clerk])

Founding Partner plan: [$1,250]/month · 12-month price lock · cancel with 30 days' notice
```

> **The numbers above are illustrative.** Always fill in **their** catches and **their** Day 0 cost estimates. Never present our assumptions as their savings. If their own math doesn't justify the price, say so; that conversation will earn more trust than any close.

### The script

> **1. Recap the deal (2 min): the founder speaks**
> "Thank you for making the time, [Owner]. Fourteen days ago we agreed on something simple: if we didn't catch at least three real paperwork errors, you'd owe nothing. Your team decided what counted as real, not us. Let's look at what they found."
>
> **2. The ledger (10 min): let the champion present**
> "[Ops Manager], would you walk [Owner] through the one you showed me on Day 7?"
> *[The clerk or Ops Manager tells the story of the best S1 catch. Hand them the printout and stay quiet. A catch explained by their own employee is worth ten explained by us.]*
> Then go through the summary: shipments checked, confirmed catches by severity, **false alarms included**.
> "We also want you to see our misses. Two times we flagged something that wasn't a problem. Here's what we fixed."
>
> **3. The math, in their words (5 min)**
> "On Day 0, [Ops Manager] told us one turned-back truck costs you about **[$650]** once you count detention, re-dray and people's time. Using your numbers, these [9] catches come to about **[$2,670]** in two weeks, or roughly **[$5,790]** a month. Does that match how it feels on the floor?"
> *[Pause. Let them adjust the number. If they lower it, accept it. Their number is the only one that matters.]*
>
> **4. What changes next (5 min)**
> "If we keep going, nothing changes for [Clerk]. Same forwarding, same number. Three things change: alerts can go to the yard if you want them, we add the [email inbox / sender] we didn't cover, and you get a monthly ledger you can keep on file as a record of your team's diligence."
>
> **5. The ask (1 min): then stop talking**
> "So, based on what we agreed: your team confirmed **[9]**, and the bar was three. I'd like to turn this on for real starting tomorrow, at the founding partner rate of **[$1,250] a month, locked for 12 months**, cancel with 30 days' notice. Shall I keep it on the same account so nothing changes for [Clerk]?"
> *[Silence. Wait. Don't fill it.]*
>
> **6. Paper it (5 min)**
> Founding Partner agreement on the tablet. Then: **"One more thing. Who's one broker you respect who has messier paperwork than you?"** Write the name down and ask whether you may mention them.

### Español (key lines)

> "Hace 14 días quedamos en algo sencillo: si no encontrábamos por lo menos tres errores reales, no nos debía nada. Y su equipo decidió qué contaba como real, no nosotros."
> "[Gerente], ¿le platica a [Dueño] el caso que me enseñó el día 7?"
> "El día 0 nos dijeron que un camión regresado les cuesta como **[$650]**. Con sus números, estos [9] errores suman **[$2,670]** en dos semanas. ¿Le parece correcto?"
> "Con base en lo que acordamos, su equipo confirmó **[9]** y la meta era tres. Me gustaría activarlo desde mañana con la tarifa de socio fundador: **[$1,250] al mes, precio fijo por 12 meses**, y lo puede cancelar con 30 días de aviso. ¿Lo dejamos en la misma cuenta para que nada cambie para [Clerk]?"

### Late-stage objections

| They say | You say |
|---|---|
| "Let me think about it." | "Of course. What would you need to see to make it an easy yes?" Then solve exactly that, and set a dated decision. |
| "It's too expensive." | "Compared to what it caught?" Go back to *their* number. Offer to start with one office or one flow, or the per-shipment option. |
| "I need to talk to my partner." | "Absolutely. May I leave a copy of the ledger for them, and come back Thursday at 10:30 to answer their questions?" |
| "Can we extend the pilot?" | Only if intake was genuinely broken (extend by the days lost, in writing). Otherwise: "The results are in. What's missing for a decision?" |
| A strategically important logo that can't pay yet | Offer a **Design Partner** slot (max 3): free for 60 days, in exchange for weekly feedback, a case study and 2 references, **converting on a date written into the agreement**. |

### If we did NOT hit 3 confirmed catches

Honor the deal immediately and graciously. This is how we earn our reputation in a small market where every broker knows every other broker.

> "We didn't hit three. You owe nothing, exactly as we agreed. Here's the ledger; it's yours. Would you like us to delete your data or send you an export? … One honest question: was it a volume thing or a fit thing? And is there a broker you know with messier intake than yours who might benefit more?"

Then delete or export within 48 hours, confirm it in writing, and send a thank-you note.

---

## 3.6 Day 15: what happens next

| Outcome | Next steps |
|---|---|
| **Signed** | Same tenant, now on the paid plan. Turn on yard alerts if wanted. Send a welcome WhatsApp with the founder's direct line. Book a **30-day review**. Ask permission for a case-study quote at day 30. |
| **Design Partner** | Calendar the weekly 20-min feedback call and the **conversion date**. |
| **Decision pending** | Dated follow-up, no more than 7 days out. Keep the pilot running only if that was agreed in writing. |
| **No** | Export or delete within 48 hours, confirmed in writing. Log the reason. Ask for one referral. Add them to the monthly "catch of the month" nurture. |

---

## 3.7 The Caught Errors Ledger

The template lives at [`caught-errors-ledger-template.csv`](caught-errors-ledger-template.csv). Import it into Google Sheets or Excel and add a **Baseline** tab for the Day 0 answers.

| Column | What goes in it |
|---|---|
| `catch_id` | C-001, C-002, … |
| `flagged_at` | Date and time of the flag |
| `shipment_ref` | Their load or reference number |
| `client_code` | **A client code, never the client's name**, to protect confidentiality |
| `corridor` | Pharr–Reynosa / Brownsville–Matamoros |
| `doc_a` / `doc_b` | e.g. CFDI factura / BOL / Carta Porte |
| `field` | Pallet count, gross weight, box count… |
| `value_doc_a` / `value_doc_b` | Exactly as written in each document, with units |
| `normalized_difference` | e.g. "−2 pallets; −1,871 kg (−10.3%)" |
| `severity` | S1 / S2 / S3 (confirmed by the customer) |
| `minutes_before_crossing` | How early we caught it |
| `alert_sent_to` | Ops Manager / yard / ledger only |
| `status` | Confirmed / False alarm / Already known / Duplicate |
| `confirmed_by` | Name of the clerk or manager |
| `action_taken` | Resend requested, corrected before filing, carrier called… |
| `customer_cost_estimate_usd` | **Their** Day 0 estimate for this severity |
| `notes` | Anything that makes the story real |
