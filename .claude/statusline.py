#!/usr/bin/env python3
"""Status line: model · context tokens used (and %) · session cost.

Context size is read from the last assistant turn in the transcript, so it
works on any Claude Code version. Turns yellow at 150k and red at 300k:
that's the cue to /compact or start a fresh session.
"""
import json
import sys

d = json.load(sys.stdin)
model = (d.get("model") or {}).get("display_name", "?")
cost = (d.get("cost") or {}).get("total_cost_usd")

used = 0
try:
    with open(d.get("transcript_path") or "") as f:
        for line in f:
            try:
                u = json.loads(line).get("message", {}).get("usage")
            except ValueError:
                continue
            if isinstance(u, dict):
                used = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                        + u.get("cache_creation_input_tokens", 0))
except OSError:
    pass

size = ((d.get("context_window") or {}).get("context_window_size")) or 1_000_000
color = "\033[31m" if used >= 300_000 else "\033[33m" if used >= 150_000 else "\033[32m"
parts = [model, f"{color}ctx {used / 1000:.0f}k ({used * 100 // size}%)\033[0m"]
if cost is not None:
    parts.append(f"${cost:.2f}")
print(" · ".join(parts))
