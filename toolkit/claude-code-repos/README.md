# Claude Code Repo Toolkit

The open-source repos from the "Just Copy Me" Claude Code walkthrough, one per video chapter, with the install command each repo's own README recommends. Every repo was checked on GitHub on 2026-10-13.

Use `install.sh` to install them, or copy single commands from the tables below.

```bash
./toolkit/claude-code-repos/install.sh --list          # show what can be installed
./toolkit/claude-code-repos/install.sh hyperframes humanizer
./toolkit/claude-code-repos/install.sh --all --dry-run # print every command, run nothing
```

## By chapter

| Chapter | Repo | What it adds |
| --- | --- | --- |
| 0:24 Video editing | [heygen-com/hyperframes](https://github.com/heygen-com/hyperframes) | Write HTML, render MP4. Skills for planning, linting, previewing and rendering video. |
| 0:24 Video editing (alternative) | [remotion-dev/skills](https://github.com/remotion-dev/skills) | Agent skills for [Remotion](https://github.com/remotion-dev/remotion), which makes videos with React. |
| 1:28 Google Workspace | [googleworkspace/cli](https://github.com/googleworkspace/cli) | `gws`, one CLI for Drive, Gmail, Calendar, Sheets, Docs and more. It needs a Google Cloud OAuth project. |
| 1:28 Apify | [apify/agent-skills](https://github.com/apify/agent-skills) | Scraping and web data through Apify Actors. |
| 3:09 Better websites | [nateherkai/scroll-craft](https://github.com/nateherkai/scroll-craft) | Builds premium, scroll-driven websites. |
| 3:09 Human copy | [blader/humanizer](https://github.com/blader/humanizer) | Removes the telltale patterns of AI writing from copy. |
| 4:57 Planning builds | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | `/spec`, `/plan`, `/build`, `/test`, `/review`, `/ship` skills that follow the dev lifecycle. |
| 6:20 Expo | [expo/skills](https://github.com/expo/skills) | Official Expo / React Native skills. |
| 6:20 Supabase | [supabase/agent-skills](https://github.com/supabase/agent-skills) | Supabase and Postgres best-practice skills. |
| 6:20 Stripe | [stripe/ai](https://github.com/stripe/ai) | The official Stripe plugin and skills for payments. |
| 7:34 Browser testing | [microsoft/playwright-cli](https://github.com/microsoft/playwright-cli) | Drives a real browser from the CLI and uses fewer tokens than the MCP server. |
| 7:34 Security | [anthropics/claude-code-security-review](https://github.com/anthropics/claude-code-security-review) | The `/security-review` command, which ships with Claude Code, plus a GitHub Action for PR security review. |
| 8:32 SEO with real data | [every-app/open-seo](https://github.com/every-app/open-seo) | An open-source Semrush/Ahrefs alternative with an MCP server and skills. You bring your own DataForSEO key. |
| 8:56 Token savers | [rtk-ai/rtk](https://github.com/rtk-ai/rtk) | Compresses command output (tests, git, ls) before it reaches the model. |
| 8:56 Token savers | [juliusbrussee/caveman](https://github.com/juliusbrussee/caveman) | Makes replies terse to cut output tokens. |
| 8:56 Usage tracking | [ryoppippi/ccusage](https://github.com/ryoppippi/ccusage) | Reports Claude Code token usage and cost from local logs. |

## Install commands

Run these in a terminal. Commands starting with `/` are typed inside a Claude Code session.

| Key | Command |
| --- | --- |
| `hyperframes` | `claude plugin marketplace add heygen-com/hyperframes` then `claude plugin install hyperframes@hyperframes` |
| `remotion` | `npx skills add remotion-dev/skills` |
| `gws` | `npm install -g @googleworkspace/cli` then `gws auth setup` |
| `apify` | `claude plugin marketplace add apify/agent-skills` then `claude plugin install apify-ultimate-scraper@apify-agent-skills` |
| `scroll-craft` | `claude plugin marketplace add nateherkai/scroll-craft` then `claude plugin install nateherk-design` |
| `humanizer` | `claude plugin marketplace add blader/humanizer` then `claude plugin install humanizer@humanizer` |
| `agent-skills` | `claude plugin marketplace add addyosmani/agent-skills` then `claude plugin install agent-skills@addy-agent-skills` |
| `expo` | `claude plugin install expo@claude-plugins-official` |
| `supabase` | `npx skills add supabase/agent-skills` |
| `stripe` | `claude plugin install stripe@claude-plugins-official` |
| `playwright` | `npm install -g @playwright/cli@latest` then `playwright-cli install --skills` |
| `security` | Built in: run `/security-review`. Copy the [command file](https://github.com/anthropics/claude-code-security-review/blob/main/.claude/commands/security-review.md) into `.claude/commands/` if you want to customize it. |
| `open-seo` | Sign up at [openseo.so](https://openseo.so) or self-host, then connect its MCP server. See the repo README. |
| `rtk` | `brew install rtk` (macOS/Linux) or `winget install rtk-ai.rtk` (Windows) |
| `caveman` | `npx skills add JuliusBrussee/caveman -g` |
| `ccusage` | `npx ccusage@latest` (nothing to install) |

## Before you install

- These are third-party repos. Read each README and skim the skill files first, because a skill runs with the same permissions as your agent.
- Some of them need accounts or API keys: Google Cloud OAuth (`gws`), an Apify token, a Stripe key, a Supabase project, and DataForSEO credits (OpenSEO).
- Install commands change. If one here fails, the repo's README is the source of truth.
