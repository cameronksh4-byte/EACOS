#!/usr/bin/env bash
# Install the Claude Code repos listed in README.md.
# Usage: install.sh [--dry-run] (--all | --list | KEY...)
set -euo pipefail

declare -A CMDS=(
  [hyperframes]="claude plugin marketplace add heygen-com/hyperframes && claude plugin install hyperframes@hyperframes"
  [remotion]="npx skills add remotion-dev/skills"
  [gws]="npm install -g @googleworkspace/cli"
  [apify]="claude plugin marketplace add apify/agent-skills && claude plugin install apify-ultimate-scraper@apify-agent-skills"
  [scroll-craft]="claude plugin marketplace add nateherkai/scroll-craft && claude plugin install nateherk-design"
  [humanizer]="claude plugin marketplace add blader/humanizer && claude plugin install humanizer@humanizer"
  [agent-skills]="claude plugin marketplace add addyosmani/agent-skills && claude plugin install agent-skills@addy-agent-skills"
  [expo]="claude plugin install expo@claude-plugins-official"
  [supabase]="npx skills add supabase/agent-skills"
  [stripe]="claude plugin install stripe@claude-plugins-official"
  [playwright]="npm install -g @playwright/cli@latest && playwright-cli install --skills"
  [caveman]="npx skills add JuliusBrussee/caveman -g"
  [ccusage]="npx ccusage@latest"
)
# Same order as the README.
ORDER=(hyperframes remotion gws apify scroll-craft humanizer agent-skills expo supabase stripe playwright caveman ccusage)

# Tools with no one-line install. The script prints these instead of running them.
declare -A MANUAL=(
  [security]="Built into Claude Code: run /security-review. See github.com/anthropics/claude-code-security-review"
  [open-seo]="Needs a DataForSEO key and MCP setup: see github.com/every-app/open-seo"
  [rtk]="brew install rtk (macOS/Linux) or winget install rtk-ai.rtk (Windows)"
)

dry_run=0
keys=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --all) keys+=("${ORDER[@]}") ;;
    --list)
      for k in "${ORDER[@]}"; do printf '%-13s %s\n' "$k" "${CMDS[$k]}"; done
      for k in "${!MANUAL[@]}"; do printf '%-13s (manual) %s\n' "$k" "${MANUAL[$k]}"; done
      exit 0 ;;
    -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
    *) keys+=("$arg") ;;
  esac
done

if [ ${#keys[@]} -eq 0 ]; then
  echo "Nothing selected. Pass --list, --all, or one or more keys." >&2
  exit 1
fi

for k in "${keys[@]}"; do
  if [ -n "${MANUAL[$k]:-}" ]; then
    echo "[$k] manual step: ${MANUAL[$k]}"
  elif [ -n "${CMDS[$k]:-}" ]; then
    echo "[$k] ${CMDS[$k]}"
    [ "$dry_run" -eq 1 ] || bash -c "${CMDS[$k]}"
  else
    echo "Unknown key: $k (run --list)" >&2
    exit 1
  fi
done
