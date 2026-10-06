"""A tiny JSON file that holds everything the agent knows.

It's a plain file on purpose: the scheduled GitHub Action commits it back to
the repo, so the history of every idea and post is visible in git.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

EMPTY = {"ideas": {}, "launch_kits": {}, "posts": []}


def default_path() -> Path:
    return Path(os.environ.get("GROWTH_AGENT_DATA",
                               Path(__file__).resolve().parent.parent / "data")) / "store.json"


def make_id(*parts: str) -> str:
    raw = "|".join(parts)
    slug = "".join(c if c.isalnum() else "-" for c in parts[0].lower()).strip("-")[:24]
    return f"{slug}-{hashlib.sha1(raw.encode()).hexdigest()[:6]}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or default_path())
        self.data = json.loads(self.path.read_text()) if self.path.exists() \
            else json.loads(json.dumps(EMPTY))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, default=str) + "\n")

    # --- ideas -----------------------------------------------------------
    def add_idea(self, idea: dict) -> str:
        idea_id = make_id(idea["name"], idea["one_liner"])
        idea.setdefault("status", "new")          # new -> launched -> paused
        idea.setdefault("created_at", now_utc().isoformat())
        self.data["ideas"][idea_id] = {"id": idea_id, **idea}
        return idea_id

    def ideas(self, status: str | None = None) -> list[dict]:
        items = self.data["ideas"].values()
        items = [i for i in items if status is None or i["status"] == status]
        return sorted(items, key=lambda i: -i.get("score", {}).get("total", 0))

    def idea(self, idea_id: str) -> dict:
        if idea_id not in self.data["ideas"]:
            raise KeyError(f"No idea with id '{idea_id}'. Run `ideas` to list them.")
        return self.data["ideas"][idea_id]

    # --- posts -----------------------------------------------------------
    def add_post(self, post: dict) -> str:
        post_id = make_id(post["platform"], post["text"], post["scheduled_for"])
        self.data["posts"].append({"id": post_id, **post})
        return post_id

    def posts(self, status: str | None = None, idea_id: str | None = None) -> list[dict]:
        return sorted(
            (p for p in self.data["posts"]
             if (status is None or p["status"] == status)
             and (idea_id is None or p["idea_id"] == idea_id)),
            key=lambda p: p["scheduled_for"])

    def post(self, post_id: str) -> dict:
        for p in self.data["posts"]:
            if p["id"] == post_id:
                return p
        raise KeyError(f"No post with id '{post_id}'.")
