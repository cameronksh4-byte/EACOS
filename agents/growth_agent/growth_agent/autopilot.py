"""Hands-off loop, run on a schedule (see .github/workflows/growth-agent.yml):

  1. publish every approved post whose time has come
  2. top up the content queue for every launched idea that's running low
"""

from datetime import datetime, timedelta

from .graphs import build_launch_graph
from .llm import LLM
from .store import Store, now_utc

MAX_ATTEMPTS = 3


def publish_due(store: Store, publisher, now: datetime | None = None,
                limit: int = 20) -> list[dict]:
    now = now or now_utc()
    results = []
    due = [p for p in store.posts(status="approved")
           if datetime.fromisoformat(p["scheduled_for"]) <= now][:limit]
    for post in due:
        post["attempts"] = post.get("attempts", 0) + 1
        try:
            post["publish_result"] = publisher.publish(post)
            post["status"] = "posted"
            post["posted_at"] = now.isoformat()
            post["publisher"] = publisher.name
        except Exception as e:  # keep going; one bad post shouldn't block the rest
            post["last_error"] = str(e)
            if post["attempts"] >= MAX_ATTEMPTS:
                post["status"] = "failed"
        results.append(post)
        store.save()                       # save after each post: never double-post
    return results


def refill_content(store: Store, llm: LLM, profile: dict,
                   now: datetime | None = None) -> dict[str, int]:
    """Plan more posts for launched ideas with fewer than `min_queue_days` left."""
    now = now or now_utc()
    social = profile.get("social", {})
    min_days = social.get("min_queue_days", 3)
    horizon = now + timedelta(days=min_days)
    graph = build_launch_graph(llm, store)
    added = {}
    for idea in store.ideas(status="launched"):
        upcoming = [p for p in store.posts(idea_id=idea["id"])
                    if p["status"] in ("approved", "pending")
                    and datetime.fromisoformat(p["scheduled_for"]) >= horizon]
        if upcoming:
            continue
        out = graph.invoke({"profile": profile, "idea_id": idea["id"]})
        added[idea["id"]] = len(out.get("queued_ids", []))
    return added
