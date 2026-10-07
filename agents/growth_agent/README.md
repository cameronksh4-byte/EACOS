# Growth Agent

A LangGraph agent, powered by Claude, that:

1. **Brainstorms** B2B ideas tailored to you. Each one is either a paid SaaS product or a **free value platform** (a calculator, audit, directory, template or community) that brings in qualified business leads.
2. **Scores and ranks** each idea on pain, willingness to pay, reach, speed to revenue and build ease, so you start with the idea most likely to earn its **first dollar**.
3. **Launches** the idea you pick. It writes a landing page with an email capture form, a lead magnet outline, an honest founding-customer offer, a 14-day launch plan and a cold-outreach script.
4. **Markets it on autopilot.** It writes platform-native posts, runs each one through an AI brand-safety review and schedules them. A GitHub Action publishes them every hour and writes more when the queue runs low.

After a one-time setup, you **never open a social media app**. The agent posts through one posting API, and you can run every command from the GitHub Actions tab, including on your phone.

```
 brainstorm ─► score ─► rank          launch ─► plan content ─► review ─► queue
                  │                                                         │
              you pick one ───────────────────────────────────────────┐    │
                                                                       ▼    ▼
                               hourly GitHub Action:  publish due posts ─► refill queue
```

## Try it now (offline, no keys)

```bash
cd agents/growth_agent
pip install -r requirements.txt
python -m growth_agent brainstorm --count 5
python -m growth_agent launch <id-from-the-list>
python -m growth_agent queue
python -m growth_agent autopilot        # dry run: posts go to data/outbox.log
python -m pytest -q
```

Without `ANTHROPIC_API_KEY`, the agent uses canned sample answers so you can see the whole flow. Set the key to get real ideas and copy from Claude:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
cp profile.example.yaml profile.yaml    # describe yourself: skills, audience, budget, values, voice
python -m growth_agent brainstorm --count 10 --focus "tools for church admins and small nonprofits"
```

## Go hands-off (one-time setup, about 15 minutes)

1. **Connect your social accounts once** in a posting service:
   - **[Ayrshare](https://www.ayrshare.com)** (recommended): one API key covers LinkedIn, X, Facebook, Instagram, Threads, Bluesky, TikTok, Pinterest, Reddit and more. Link your accounts in its dashboard and copy the API key.
   - **Webhook**: point the agent at a Zapier, Make or n8n scenario. It receives each post as JSON and publishes it through that tool's own social integrations.
2. **Commit your `profile.yaml`.** Set `social.platforms`, `timezone` and `approval`.
3. In GitHub, go to **Settings → Secrets and variables → Actions** and add:
   - Secret `ANTHROPIC_API_KEY`
   - Secret `AYRSHARE_API_KEY` (or `GROWTH_AGENT_WEBHOOK_URL`)
   - Variable `GROWTH_AGENT_PUBLISHER` = `ayrshare` (or `webhook`)
   - Variable `GROWTH_AGENT_ENABLED` = `true`. This turns on the hourly autopilot.
4. From **Actions → growth-agent → Run workflow**, run `brainstorm --count 10`. Check the run summary for the ranked list, then run `launch <id>`.

That's it. Every hour the workflow publishes the posts that are due, writes the next week of posts when fewer than `min_queue_days` remain, and commits `data/store.json` so the full history is in git.

## Commands

| Command | What it does |
|---|---|
| `brainstorm [--count N] [--focus TEXT]` | Generate, score and rank new ideas (skips ideas it already has) |
| `ideas` | List all ideas, best first |
| `launch ID [--rebuild]` | Launch kit + `data/sites/ID/index.html` + first week of posts |
| `queue [--status S]` | Show scheduled posts and their status |
| `approve ID\|all` / `reject ID` | Only needed with `approval: manual` |
| `pause ID` / `resume ID` | Stop or restart all posting for an idea |
| `publish` | Publish posts that are due now |
| `autopilot` | `publish`, then refill content (what the hourly job runs) |

## Safety rails (because nobody checks each post)

- Every post goes through a **separate reviewer pass** that rejects or fixes invented statistics, fake testimonials, spammy or manipulative tone, platform-rule risks and off-voice copy. A post the reviewer didn't explicitly approve is held back.
- The prompts forbid fake scarcity and inflated claims. Your `values` and `voice` in `profile.yaml` steer everything.
- Prefer to see posts first? Set `approval: manual`. Posts then wait as `pending` until you run `approve all`, which you can also do from the Actions tab.
- The default publisher is `dryrun`. Nothing goes live until you choose `ayrshare` or `webhook`.
- Posts are saved after each publish, and runs never overlap, so a post can't go out twice. Failed posts retry up to 3 times.

## Leads → revenue

`launch` writes a static landing page whose email form posts to `lead_capture.form_action`. That can be Formspree, Tally, ConvertKit or any similar form endpoint. Host the page on GitHub Pages, Netlify or Cloudflare Pages, then set `social.link_template` so every post links to it. The 14-day plan and outreach script cover the part no social post can: talking to the first ten customers yourself.

## Files

```
growth_agent/
  graphs.py      the two LangGraph workflows (brainstorm, launch/refill)
  schemas.py     structured outputs Claude fills in
  prompts.py     strategist, copywriter and reviewer instructions
  autopilot.py   publish due posts + refill the queue
  publishers.py  dryrun | ayrshare | webhook
  landing.py     landing page generator
  store.py       data/store.json (ideas, kits, posts)
  cli.py         command line
```
