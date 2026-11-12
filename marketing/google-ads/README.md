# Valley Agent Works: Google Ads Launch Plan

**Budget:** $112 over 20 days (about $5.50/day)
**Goal:** booked free strategy calls
**Market:** AI automation services for small businesses, targeting one state
**Website:** https://valleyagentworks.com/

---

## What to expect from this budget

With $112, results are about learning what works, not volume. AI and automation keywords usually cost **$3–$12 per click** (check your state's numbers in Keyword Planner during setup). That works out to roughly **15–35 clicks over 20 days**, and if the landing page is strong, about **1–3 booked calls**. One new client would more than pay for the test. Either way, after 20 days you'll know which searches bring real buyers, so the next dollar works harder.

To make a small budget go far, this plan:

1. **Runs one Search campaign only.** No Display, Search Partners, Performance Max or broad match. Those spread a small budget too thin.
2. **Uses exact and phrase match** so ads only show for searches from people looking to *hire* help.
3. **Starts with 83 negative keywords.** Most people searching "AI automation agency" want to *start* an agency (guru courses), and "agent" also brings in insurance and real estate searches. The negatives filter both out.
4. **Runs only when buyers are searching:** business hours, plus Saturday morning.

---

## Files in this folder

| File | What it is |
|---|---|
| `keywords.csv` | 43 keywords across 4 ad groups (Google Ads Editor import) |
| `negative_keywords.csv` | 83 campaign-level negative keywords |
| `responsive_search_ads.csv` | 1 responsive search ad per ad group (15 headlines, 4 descriptions) |
| `build_ads.py` | Regenerates the CSVs and checks every character limit. Edit the copy here, then run `python build_ads.py .` |

---

## Step 1: Create the account (about 15 min)

1. Go to **ads.google.com** and sign in with the Google account you use for the business.
2. Google will push you into a "Smart" campaign. **Don't build it.** Look for **"Switch to Expert Mode"** (bottom of the first screen), then choose **"Create an account without a campaign."**
3. Confirm the time zone and currency. **Neither can be changed later.**
4. Add billing. If a new-advertiser promo credit is offered, read its terms. Most require spending more than $112, so don't count on it.

## Step 2: Set up conversion tracking first (about 20 min)

Without it, you can't tell which keywords bring booked calls. **Don't skip this step.**

1. **Goals → Conversions → New conversion action → Website.** Enter `valleyagentworks.com`.
2. Create a conversion called **"Booked strategy call"**: Category *Book appointment*, Value *Don't use a value* (or your estimated deal value), Count *One*, Click-through window *30 days*. Make it a **Primary** action.
3. Install the **Google tag** on every page of the site. Most website builders (Wix, Squarespace, WordPress, Webflow) have a "Google tag" or header-code box.
4. Fire the conversion when someone **finishes** booking:
   - **Calendly (paid plan):** under the event type's *Booking page options*, redirect to a page on your site such as `valleyagentworks.com/thanks`. Then set the conversion to fire on **page load** of URLs containing `/thanks`.
   - **Free Calendly / Google appointment booking:** keep the booking link but make the "Book a call" button go to a page you control, and track a click on the booking button as a **Secondary** conversion named "Clicked book a call." This is less precise, but it's still a signal.
5. Use **Tag Assistant** (tagassistant.google.com) to confirm the tag fires. Then book a test call yourself and check that the conversion appears within 24 hours.

## Step 3: Create the campaign (about 20 min)

**Campaigns → + New campaign → "Create a campaign without a goal's guidance" → Search.**

| Setting | Value | Why |
|---|---|---|
| Campaign name | `VAW \| Search \| AI Automation \| Book a Call` | Must match the CSVs exactly |
| Networks | **Uncheck** Search Partners and **uncheck** Display Network | Keeps all $112 on Google Search |
| Locations | **Your state** | |
| Location options | **"Presence: people in or regularly in your included locations"** | The default also shows ads to people outside the state who are "interested" in it |
| Languages | English | |
| Budget | **Total campaign budget $112** with start and end dates, if offered. Otherwise **$5.50/day** | A total budget can't overspend. Google can spend up to 2× the daily budget on a busy day, so watch pacing on Day 10 |
| Bidding | **Maximize clicks**, with **max CPC bid limit $4.00** | A new account has no conversion data for Smart Bidding. The cap stops one click from eating a day's budget |
| Start / end date | Today → **today + 20 days** | Stops spend automatically |
| Ad schedule | Mon–Fri 7:00 AM–8:00 PM, Sat 8:00 AM–1:00 PM, Sun off | Business owners hire on weekdays. Sunday searches are the lowest-intent for B2B |
| AI Max / broad match | **Off** | Broad match drifts badly on "agent" and "automation" |
| Automatically created assets | **Off** | Keeps ad copy honest and accurate |
| Auto-apply recommendations | **Off** (Recommendations → Auto-apply) | Many auto-applied recommendations raise spend or loosen targeting |

## Step 4: Load keywords, negatives and ads (about 15 min)

**Option A: Google Ads Editor (fastest).** Download Google Ads Editor (free), sign in and download the account. Then **Account → Import → From file** for each CSV in this order: `keywords.csv`, `negative_keywords.csv`, `responsive_search_ads.csv`. Review the changes, then click **Post**.

**Option B: Web interface.** Create the 4 ad groups below, paste each group's keywords (exact as `[keyword]`, phrase as `"keyword"`), paste the negatives under **Keywords → Negative keywords → Campaign**, and copy the headlines and descriptions from `responsive_search_ads.csv`.

### Ad groups

| Ad group | Example keywords | Priority |
|---|---|---|
| **AI Automation Services** | ai automation services, ai consultant near me, workflow automation consultant | Main group |
| **Custom AI Agents** | custom ai agent, ai agent development, ai agents for small business | Main group |
| **Zapier Make n8n Experts** | zapier expert, n8n consultant, make.com expert | Often the cheapest, highest-intent clicks. **Pause it if you don't do this work** |
| **Brand** | valley agent works | Pennies per click. Protects your name as you grow |

## Step 5: Add assets (about 10 min)

Assets make the ad bigger and raise click-through rate at no extra cost. Add them at the **campaign** level.

**Sitelinks.** Point each one at a real page. If a page doesn't exist yet, use the homepage with an anchor such as `/#services`:

| Text | Description line 1 | Description line 2 |
|---|---|---|
| Book a Free Call | Pick a time that works for you | See if automation fits |
| What We Automate | Lead follow-up, admin, reports | Real examples, plain English |
| Custom AI Agents | Agents built for your workflow | Connected to tools you use |
| About Valley Agent Works | Meet the builder | How we work with clients |

**Callouts:** Free Consultation · Small Business Focused · Plain-English Updates · Works With Your Tools · Honest Recommendations · Talk to the Builder

**Structured snippet** (Header: *Services*): AI Agents, Workflow Automation, Lead Follow-Up, CRM Automation, Zapier & Make, Reporting

**Call asset:** your business phone, scheduled during ad hours only, if you'll answer it.

**Optional:** add a headline such as `Serving [Your State] Businesses` to each ad. Local trust lifts click-through rate.

> **Keep every claim true.** The ads promise a **free consultation call**. If the call isn't free, change "Free Consultation Call" and "Book a Free Strategy Call" in `build_ads.py` and regenerate. Google rejects ads whose claims the landing page doesn't support, and so does your reputation.

## Step 6: Landing page checklist

I couldn't load valleyagentworks.com from this session, so check these against your own site. **The page converts the clicks; the ads only bring them in.**

- [ ] **"Book a free call" button above the fold** on mobile and desktop, linking straight to booking
- [ ] The headline matches the ads, for example "AI automation for small businesses in [State]"
- [ ] **2–3 concrete examples** of what you automate, with before and after (e.g. "New leads get a reply in 2 minutes, not 2 days")
- [ ] **Your face and name.** For a small service business, people are hiring *you*
- [ ] What happens on the call, in one sentence ("30 minutes, we pick one process, no pressure")
- [ ] Loads in under 3 seconds on a phone (test at pagespeed.web.dev)
- [ ] A privacy policy link in the footer
- [ ] No claims you can't back up. Google's ad review and visitors both check

---

## 20-day optimization calendar (about 15 min per check-in)

| Day | Do this |
|---|---|
| **0** | Launch. Confirm all 4 ads say **"Eligible"** or **"Approved"** (review takes up to 1 business day). |
| **1–2** | **Campaigns → Keywords:** any impressions? If almost none, raise the max CPC limit to $5–6. If a keyword shows "Low search volume," leave it; it costs nothing. |
| **3** | **First search terms review** (Insights & reports → Search terms). Add every irrelevant term as a **negative**. This is the most valuable 10 minutes of the whole test. |
| **5** | Search terms again. Check for spelling or policy disapprovals. |
| **7** | Week-1 review: which ad group gets clicks? CTR under 3% on a keyword means the ad doesn't match the search, so tighten it or pause it. |
| **10** | **Pacing check:** spend should be about $56. If it's ahead, lower the daily budget. Pause any keyword that has spent **$15+ with 0 conversions and 0 booking-page visits**. |
| **12** | Search terms again. Add the best-converting search terms as **exact match** keywords. |
| **14** | Shift effort to the winner: pause the weakest ad group and let budget flow to the one producing calls. Check **Ads → Asset details**, and replace any headline rated "Low" once it has data. |
| **17** | Last search terms sweep. Follow up personally with everyone who booked. |
| **20** | Campaign ends. Record results in the scorecard below, then decide: renew, adjust, or pause. |

### Scorecard (fill in on Day 20)

| Metric | Result | Notes |
|---|---|---|
| Spend | $ | |
| Clicks / avg CPC | / $ | |
| CTR | % | 5%+ is healthy for B2B search |
| Booked calls | | |
| Cost per booked call | $ | |
| Calls → clients | | |
| Best ad group | | |
| Best 3 search terms | | |
| Negatives added | | |

**After Day 20:** if you got **1+ booked calls**, renew with the same structure and spend only on the winning ad group. Once you reach **15+ conversions in 30 days**, switch bidding to **Maximize conversions** (and later Target CPA). If you got **0 calls but healthy clicks**, the landing page is the bottleneck. Fix it before spending more. If you got **few clicks**, raise the CPC cap or add more phrase-match keywords from the search terms report.

---

## Rules of thumb for a small budget

- **Don't change too much at once.** Give each change about 3 days before judging it.
- **Don't accept every recommendation** in the Recommendations tab. Many are built to raise spend.
- **The search terms report is your best tool.** Check it every few days.
- **Respond to booked calls within an hour.** Speed to lead matters more than any bid change.
