# RGV Pre-Bridge Check: 90-Day Go-To-Market Execution Plan

**Goal:** 10 signed customs-brokerage customers (at least 7 paying, no more than 3 design partners with a dated conversion) across the **Pharr–Reynosa** and **Brownsville–Matamoros** corridors by **Day 90 (Saturday, January 16, 2027)**.

**Motion:** 100% face-to-face. We walk in, we offer a 14-day Zero-IT Shadow Pilot, and we let the Caught Errors Ledger close the deal.

**What we sell:** recovered revenue, protection for the broker's license, and less stress for the clerks. We do not sell features.

> **Working name:** this plan calls the product **CruceCheck** ("cruce" means crossing). It's a placeholder, so find-and-replace it with your real brand. Bracketed fields like `[Rep Name]` and `[$1,250]` are yours to fill in.

| # | Document | Use it for |
|---|---|---|
| 1 | [Walk-In Playbook](01-walk-in-playbook.md) | Timing, routes, the 60-second pitch (EN/ES), discovery, the phone demo, the objection matrix |
| 2 | [Leave-Behind One-Pager](02-leave-behind-one-pager.md) | Print-ready copy, English front and Spanish back, plus print specs |
| 3 | [14-Day Shadow Pilot Blueprint](03-shadow-pilot-blueprint.md) | 10-minute setup, catch definition, Day 7 and Day 14 scripts, ROI math |
| 4 | [Follow-Up Sequences](04-follow-up-sequences.md) | 3-touch WhatsApp and email sequence (EN/ES) for "send me more info next week" |
| – | [Caught Errors Ledger template](caught-errors-ledger-template.csv) | The spreadsheet that closes every deal |

---

## 0. Read this first: claims to fix before anything gets printed

Brokers check paperwork for a living, and they'll check ours. One wrong claim on a printed sheet loses the room. Every item below came out of the strategy brief and needs fixing or verifying **in Week 1, before printing**.

| # | Claim in the brief | The problem | What to print instead |
|---|---|---|---|
| 1 | "AWS us-east-1 / Texas" | `us-east-1` is Northern Virginia. AWS has no full region in Texas; Dallas and Houston are Local Zones only. | "Hosted only in the United States (AWS us-east-1)." Mention Texas only if data actually sits in a Texas facility. |
| 2 | "Satisfies 19 CFR § 111.24" | The confidentiality duty in § 111.24 belongs to the **broker**, and no vendor can satisfy it for them. The section most on point for U.S.-soil hosting is **§ 111.23** (where broker records are kept). | "Built to support your § 111.23 / § 111.24 recordkeeping and confidentiality obligations." Back it with a signed confidentiality and service-provider agreement. |
| 3 | "Zero-Data-Retention AI" next to "5-year audit trail" | Read side by side, these look contradictory. | Spell out both. **AI processors** keep nothing. **Your records** are kept in your U.S. vault for 5 years (Part 163), and you can export them any time. |
| 4 | "12-second check" | Brokers will time it. | Print "about 12 seconds" only if your **median**, measured on real phone photos of facturas sent over LTE from a yard, is 12 seconds or less. |
| 5 | "Add our bot to their WhatsApp intake group" | Meta's official WhatsApp Business Platform has historically **not** let a business number join a group the customer created. Automating a regular WhatsApp account breaks Meta's terms, and the number can get banned in the middle of a pilot. | Default flow: clerks **forward** documents to our 1:1 WhatsApp Business number, which takes two taps. Offer group intake only through an official Meta pathway your engineering lead has confirmed. |
| 6 | "They simply auto-forward email" | **Microsoft 365 blocks automatic external forwarding by default** (bounce code `550 5.7.520`). In those offices, "Zero-IT" turns into an IT ticket. | Use the fallback tree in the [Pilot Blueprint](03-shadow-pilot-blueprint.md#minute-46-email-intake). Never promise auto-forwarding until you've seen it work in their mailbox. |
| 7 | Naming Nuvocargo and BorderFlow as poachers | Printed claims about named competitors invite legal letters, and they make us look small. | Keep competitor names off paper. Put the Broker-Neutral Pledge **in the contract** and say it out loud. Let the broker connect the dots. |
| 8 | Client data sent to a vendor | § 111.24 limits what a broker may disclose about clients. | Get a confidentiality and service-provider agreement signed **before the first document arrives**. Give them a one-page compliance memo and tell them to have their attorney review it. |
| 9 | "License protection under CBP Reasonable Care" | Reasonable care is formally the **importer's** duty (19 U.S.C. § 1484). Brokers owe **responsible supervision and control** (§ 111.28) and **due diligence** (§ 111.29). Promising "license protection" is promising an outcome nobody controls. | Say: "a timestamped record of the diligence your team already does, and fewer errors reaching ACE." Never say "guaranteed compliant." |

**Rule of thumb:** if it's printed, it must be true on the day it's handed over. Leave a smaller true claim in place before you print a bigger one you can't stand behind.

---

## 1. Who we're selling to

### Ideal customer profile (Tier A, the first 10 customers come from here)

- **Independent, locally owned U.S. customs brokerage** with 5–40 staff, filing at **Port 2305 (Hidalgo/Pharr)** and/or **Port 2301 (Brownsville)**.
- Volume of **30–300 entries a day**, heavy in **produce, cold chain, or maquila** freight.
- Paperwork arrives **by WhatsApp and email** from Mexican agencias aduanales, transportistas and shippers. That messy intake is the pain we fix.
- The owner is a **Licensed Customs Broker (LCB)** who still walks the floor.
- Has had a recent bad day: a truck turned back, a hold, a Post Summary Correction, or an angry client.

### Tier B: cold-storage 3PLs and cross-dock yards (Pharr especially)
They feel the pain as **detention, blocked dock doors and reefer fuel** while a truck waits on paperwork. They're good secondary customers and a strong **referral channel** to their brokers. Use the 3PL pitch variant in the Playbook.

### Tier C: drayage and transfer carriers
These are practice visits and referral sources only. Don't spend pilot slots here in this 90 days.

### Disqualify (politely)
- Brokerages owned by forwarders or digital freight companies that have in-house developers.
- Offices filing fewer than ~10 entries a day, where three errors in 14 days is unlikely and the pilot fails for reasons that aren't ours.
- Anyone who wants us to file, transmit, or "just handle it." We're software.

### The buying group inside a brokerage

| Role | What they care about | Our job with them |
|---|---|---|
| **Owner / LCB** (economic buyer) | License, client retention, margin | Day 0 permission, Day 14 decision |
| **Operations Manager** (champion) | Trucks moving, fewer fires, looking good to the owner | Runs the pilot with us and gets Day 7 credit |
| **Lead clerk** ("la que sabe todo," the one who knows everything) | Not being blamed, not being replaced, fewer 6 a.m. surprises | Daily user. Make her the hero of every catch. |
| **Front desk** (gatekeeper) | Protecting a busy boss | Treat with respect, learn their name, ask when the quiet hour is |

---

## 2. Funnel math: what 10 customers takes

These assumptions are conservative. Re-baseline them every Friday with real numbers.

| Stage | Target | Assumed conversion |
|---|---|---|
| Target accounts on the list | 150–250 | Build in Week 1 from CBP's broker-by-port listings, a Google Maps sweep of the corridors, and the Pharr bridge area |
| Walk-in visits (incl. 2nd visits) | **~135** | ~15–20 a week in prime weeks |
| Real conversations with an Owner, LCB or Ops Manager | **~65** | ~50% of visits |
| Pilot offers accepted | **~25** | ~40% of conversations |
| **Pilots actually started** | **16** | ~65% of accepts (some stall on paperwork or season) |
| Pilots that hit 3 confirmed catches | ~12–13 | ~80% |
| **Signed customers** | **10** | ~75–80% of successful pilots |

**The calendar forces front-loading.** A 14-day pilot started after about **December 4** runs into the Christmas–New Year crunch and won't close cleanly before Day 90. So **all 16 pilots must start by Friday, December 4**, and roughly 80% of walk-ins happen in Weeks 2–7.

**Staffing assumption:** one bilingual field rep (or the founder), a part-time pilot analyst who verifies every flag, and the founder at every Day 14 meeting. That's about 1.5–2 people. **If you're solo, halve the weekly visit targets and expect to land 5–6 customers by Day 90, with the other pilots closing in January.**

---

## 3. The 90-day calendar

Day 1 = **Monday, October 19, 2026**. Day 90 = **Saturday, January 16, 2027**.

| Week | Dates | Focus | Visits | Pilots started | Cumulative signed |
|---|---|---|---|---|---|
| **1** | Oct 19–24 | **Prep.** Fix the claims in §0. Have counsel review the pilot agreement, confidentiality/service-provider addendum and compliance memo. Print the kit. Build the target list and tier it A/B/C. Set up the CRM. Pre-provision 10 pilot kits. Test M365 and Gmail forwarding. Rehearse the pitch 20 times out loud, in both languages. Friday: 5 **practice** walk-ins at Tier C accounts. | 5 | 0 | 0 |
| **2** | Oct 26–31 | **Pharr blitz.** S. Jackson Rd, Cage Blvd, Military Hwy, the bridge approach. Tue–Thu, prime windows only. | 20 | 2 | 0 |
| **3** | Nov 2–7 | Pharr Tue/Wed, **Brownsville Thursday** (Port of Brownsville / Hwy 48, the Veterans and Los Indios bridge areas). Note: Mon Nov 2 is Día de Muertos, so make it an admin and follow-up day. | 20 | 3 | 0 |
| **4** | Nov 9–14 | Walk-ins continue. **Cohort A Day 7 check-ins.** Start asking every pilot for a referral. | 20 | 3 | 0 |
| **5** | Nov 16–21 | **Cohort A Day 14 closes.** First customers. Mon Nov 16 is a Mexican federal holiday (Revolución, observed), so expect different volume. | 15 | 3 | **2** |
| **6** | Nov 23–28 | **Thanksgiving week.** No cold walk-ins Wed–Fri. Run follow-ups, support pilots, start warm or referral pilots Mon/Tue only. | 6 | 2 | **4** |
| **7** | Nov 30–Dec 5 | **Last pilot starts (deadline Fri Dec 4).** Referral visits. Cohort B closes. | 15 | 3 | **5** |
| **8** | Dec 7–12 | Closes and referrals. Sat Dec 12 is Día de la Virgen de Guadalupe, a meaningful day for many families here, so don't schedule anything. | 10 | 0 | **7** |
| **9** | Dec 14–19 | **Final pre-holiday closes.** Get anything still open onto a dated decision in January. | 0 | 0 | **8** |
| **10–11** | Dec 21–Jan 2 | **Peak-season crunch. No sales visits.** Support live customers, because this is when catches are worth the most. Capture case-study data. Drop off a dozen tamales or a holiday card to live customers as a thank-you, with no pitch. | 0 | 0 | 8 |
| **12** | Jan 4–9 | Follow up with everyone who said "after the season." Convert design partners on their dated terms. Start the Q1 pilot cohort; these close after Day 90 and seed the next quarter. | 15 | (Q1) | **9** |
| **13** | Jan 11–16 | Close the stragglers: partner approvals, extended pilots. Publish the first case study (with permission). 90-day retro. | 10 | (Q1) | **10** |

### Weekly operating rhythm

| Day | What happens |
|---|---|
| **Monday** | Route planning, follow-up sequences, pilot health checks (no cold walk-ins, because Monday mornings are weekend backlog) |
| **Tue–Wed** | Pharr corridor walk-ins in the prime windows. Day 7 and Day 14 meetings. |
| **Thursday** | Brownsville corridor walk-ins and meetings |
| **Friday** | Morning: follow-ups and second visits. Afternoon: CRM cleanup, ledger QA, **pipeline review** (30 min, every metric below). |
| **Daily** | Log every visit in the CRM **within 15 minutes**. The analyst reviews every pilot flag within 1 business hour. |
| **Sunday** | Rest. No visits and no follow-up messages, ever. People remember who respected their weekend. |

---

## 4. Offer and pricing (hypotheses to test)

| Offer | Terms |
|---|---|
| **14-Day Zero-IT Shadow Pilot** | Free to start. If we don't catch **at least 3 confirmed paperwork errors** (catch definition agreed on Day 0), they owe nothing, and they keep the ledger. If we do, the Founding Partner plan starts on Day 15. |
| **Founding Partner plan** (first 10 customers only, which is true scarcity, so we can say it) | **[$1,250]/month per office**, fair use up to [3,000] shipments a month, **price locked 12 months**, month-to-month, 30 days' notice to cancel. Includes a direct WhatsApp line to the founder, priority on the CargoWise/NetCHB export roadmap, and a voice in what we build next. |
| **Design Partner** (max 3, reserved for strategically important logos) | Free for 60 days after the pilot. In exchange: a 20-minute weekly feedback call, a case study, and 2 reference calls. They **convert on a date written into the agreement**. |
| **Fallback if they resist a flat fee** | [$2.00] per checked shipment, [$500] monthly minimum |

**Price-sanity anchor:** brokers bill per entry. At about 1,000 entries a month, [$1,250] comes to about $1.25 per entry, which is less than the cost of one truck sent back over a paperwork error. Always do the ROI in **their** numbers (see the [Day 14 script](03-shadow-pilot-blueprint.md#35-day-14-roi-reveal--close-meeting)).

---

## 5. Scoreboard: review every Friday

| Metric | Target | Why it matters |
|---|---|---|
| Walk-in visits / week | 15–20 (prime weeks) | Activity drives everything else |
| Decision-maker conversation rate | ≥ 50% of visits | If it's low, you're visiting at the wrong times |
| Pilot accept rate | ≥ 40% of conversations | If it's low, the pitch or offer isn't landing |
| **Median setup time** | **≤ 10 min** | The "Zero-IT" promise |
| Intake coverage | ≥ 70% of their real volume flowing to us by Day 3 | Low coverage means too few catches |
| **Flag precision** (confirmed ÷ flagged) | **≥ 80%** | One false alarm too many and they mute us |
| Median check latency | ≤ 12 s | The number on the one-pager |
| Day 14 meetings held **with the Owner/LCB present** | 100% | No decision maker, no close |
| Pilot → paid conversion | ≥ 60% | The proof that the pilot design works |
| Referral asks / referrals received | Ask 100% of pilots / ≥ 0.5 per customer | Cheapest route to the next customer |

### Adjust-or-stop triggers

- **Precision under 70% after Cohort A:** pause new pilots and fix extraction first, especially on phone photos and thermal-printed BOLs.
- **Median setup over 15 minutes:** simplify. Go WhatsApp-only and add email on Day 3.
- **Decision-maker rate under 30% after Week 3:** change visiting times, book second visits through the front desk, and lean on referrals.
- **Pilots hitting 3 catches but not converting:** it's a value-story or pricing problem, not a product problem. Interview every "no" in person.
- **Fewer than 5 pilots started by end of Week 4:** the plan won't reach 10. Bring in warm introductions (Texas International Produce Association members, local Rotary and chamber contacts, the brokers you already know) and add field hours.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| WhatsApp number restricted or banned mid-pilot | Use only the official WhatsApp Business Platform (or the Business app on a rep phone for 1:1 follow-ups). Keep a backup number provisioned. Email intake is the fallback. |
| Phone photos of facturas are blurry or cut off | Flag "unreadable, ask for a resend" as a feature, not a failure. **Ask senders to include the CFDI XML.** CFDI 4.0 is XML underneath and the PDF is only a printed representation, so the XML gives exact fields. |
| False alarms annoy the floor | Run silently for Days 1–3. The analyst verifies every flag. Alert only on S1/S2 severity. Track precision in the ledger. |
| Peak season ("no tenemos tiempo," we don't have time) | The setup is 10 minutes at their quietest hour and nobody's workflow changes. If they still say no, take a dated "after season" follow-up and honor it. |
| A broker worries about client confidentiality | Signed agreement before the first document, the compliance memo, and an invitation to have their attorney review it. Never pressure past this. |
| We look like "another tech startup" | Local people, bilingual, in person, boots-on-the-ground language, no jargon, and every promise in writing. |

---

## 7. Field kit checklist (assemble in Week 1)

- [ ] 100 one-pagers (English front, Spanish back). See [print specs](02-leave-behind-one-pager.md#print-specs).
- [ ] 20 printed **pilot agreements** with the confidentiality/service-provider addendum, plus an e-sign link on the tablet
- [ ] 20 copies of the **one-page compliance memo** (U.S. hosting, ZDR, retention, data use, broker-neutral pledge, what we never do), reviewed by counsel
- [ ] 10 **pre-provisioned pilot kits**. Each is an envelope holding a QR card that links to its own tenant, a unique email intake address, and laminated desk cards (EN/ES).
- [ ] Phone with the **2-minute demo**: real, sanitized mismatch examples (pallet count, kg vs lb, box count)
- [ ] Business cards with a **WhatsApp QR**
- [ ] Notebook (writing by hand in front of someone shows you're listening), plus the CRM app on the phone
- [ ] Optional: a box of pan dulce from a local panadería for second visits or a front desk that helped you. Make it a thank-you, never bait.
