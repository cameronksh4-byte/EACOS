"""Where posts go.

You connect your social accounts ONCE to a posting service, give the agent its
API key, and never open the social apps again:

  * ayrshare - one API for LinkedIn, X, Facebook, Instagram, Threads, Bluesky,
               TikTok, Pinterest, Reddit, YouTube... (https://www.ayrshare.com)
  * webhook  - POSTs each post as JSON to a URL, e.g. a Zapier / Make / n8n
               scenario that publishes with that tool's social integrations.
  * dryrun   - prints and logs posts to data/outbox.log. The safe default.
"""

import json
import os
from pathlib import Path

import requests


class PublishError(Exception):
    pass


class DryRunPublisher:
    name = "dryrun"

    def __init__(self, log_path: Path):
        self.log_path = log_path

    def publish(self, post: dict) -> dict:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as f:
            f.write(json.dumps({k: post[k] for k in ("id", "platform", "text")}) + "\n")
        print(f"[dry run] {post['platform']}: {post['text'][:80]}...")
        return {"dry_run": True}


class AyrsharePublisher:
    name = "ayrshare"
    URL = "https://api.ayrshare.com/api/post"

    def __init__(self, api_key: str, platform_map: dict | None = None):
        self.api_key = api_key
        # Our platform names -> Ayrshare's (check their docs if one changes).
        self.platform_map = {"x": "twitter", **(platform_map or {})}

    def publish(self, post: dict) -> dict:
        body = {"post": post["text"],
                "platforms": [self.platform_map.get(post["platform"], post["platform"])]}
        if post.get("media_urls"):
            body["mediaUrls"] = post["media_urls"]
        r = requests.post(self.URL, json=body, timeout=30,
                          headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code >= 400:
            raise PublishError(f"Ayrshare {r.status_code}: {r.text[:300]}")
        return r.json()


class WebhookPublisher:
    name = "webhook"

    def __init__(self, url: str, secret: str | None = None):
        self.url, self.secret = url, secret

    def publish(self, post: dict) -> dict:
        headers = {"X-Growth-Agent-Secret": self.secret} if self.secret else {}
        r = requests.post(self.url, json=post, headers=headers, timeout=30)
        if r.status_code >= 400:
            raise PublishError(f"Webhook {r.status_code}: {r.text[:300]}")
        return {"status": r.status_code}


def get_publisher(social_cfg: dict, data_dir: Path):
    kind = os.environ.get("GROWTH_AGENT_PUBLISHER", social_cfg.get("publisher", "dryrun"))
    if kind == "ayrshare":
        key = os.environ.get("AYRSHARE_API_KEY")
        if not key:
            raise PublishError("publisher=ayrshare but AYRSHARE_API_KEY is not set")
        return AyrsharePublisher(key, social_cfg.get("platform_map"))
    if kind == "webhook":
        url = os.environ.get("GROWTH_AGENT_WEBHOOK_URL")
        if not url:
            raise PublishError("publisher=webhook but GROWTH_AGENT_WEBHOOK_URL is not set")
        return WebhookPublisher(url, os.environ.get("GROWTH_AGENT_WEBHOOK_SECRET"))
    return DryRunPublisher(data_dir / "outbox.log")
