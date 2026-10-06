"""Runs fully offline: FakeLLM stands in for Claude, publishers are stubs."""

from datetime import datetime, timedelta

import pytest

from growth_agent.autopilot import MAX_ATTEMPTS, publish_due, refill_content
from growth_agent.graphs import build_brainstorm_graph, build_launch_graph
from growth_agent.landing import render_landing
from growth_agent.llm import FakeLLM
from growth_agent.schemas import PostReview, ReviewList
from growth_agent.store import Store, now_utc

PROFILE = {
    "founder": {"skills": ["python"]},
    "social": {"platforms": ["linkedin", "x"], "plan_days": 3, "min_queue_days": 2,
               "timezone": "America/New_York", "approval": "auto"},
}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store.json")


def launched(store, llm=None, profile=PROFILE):
    llm = llm or FakeLLM()
    out = build_brainstorm_graph(llm, store).invoke({"profile": profile, "count": 2})
    idea_id = out["saved_ids"][0]
    launch = build_launch_graph(llm, store).invoke({"profile": profile, "idea_id": idea_id})
    return idea_id, launch


class Recorder:
    name = "recorder"

    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def publish(self, post):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append(post["id"])
        return {"ok": True}


def test_brainstorm_scores_and_ranks(store):
    out = build_brainstorm_graph(FakeLLM(), store).invoke({"profile": PROFILE, "count": 4})
    ideas = store.ideas()
    assert len(out["saved_ids"]) == len(ideas) == 4
    totals = [i["score"]["total"] for i in ideas]
    assert totals == sorted(totals, reverse=True)
    assert all(0 < t <= 100 for t in totals)
    assert Store(store.path).ideas()  # persisted to disk


def test_launch_builds_kit_and_schedules_posts(store):
    idea_id, out = launched(store)
    assert store.idea(idea_id)["status"] == "launched"
    assert idea_id in store.data["launch_kits"]
    posts = store.posts(idea_id=idea_id)
    assert len(posts) == len(out["queued_ids"]) == 3 * 2
    assert {p["status"] for p in posts} == {"approved"}
    assert all(datetime.fromisoformat(p["scheduled_for"]) > now_utc() for p in posts)
    assert {p["platform"] for p in posts} == {"linkedin", "x"}


def test_manual_approval_holds_posts(store):
    profile = {**PROFILE, "social": {**PROFILE["social"], "approval": "manual"}}
    idea_id, _ = launched(store, profile=profile)
    assert {p["status"] for p in store.posts(idea_id=idea_id)} == {"pending"}


def test_review_can_reject_and_revise(store):
    class StrictLLM(FakeLLM):
        def structured(self, system, prompt, schema, hints=None):
            if schema is ReviewList:
                return ReviewList(reviews=[
                    PostReview(index=0, approved=False, issues=["invented statistic"]),
                    PostReview(index=1, approved=True, revised_text="Fixed copy."),
                ])  # every other post is never reviewed -> treated as rejected
            return super().structured(system, prompt, schema, hints)

    idea_id, _ = launched(store, llm=StrictLLM())
    posts = store.posts(idea_id=idea_id)
    by_text = {p["text"]: p for p in posts}
    assert by_text["Fixed copy."]["status"] == "approved"
    assert sum(p["status"] == "approved" for p in posts) == 1
    assert any(p["review_issues"] == ["invented statistic"] for p in posts)


def test_publish_only_due_posts(store):
    idea_id, _ = launched(store)
    first = store.posts(idea_id=idea_id)[0]
    pub = Recorder()
    publish_due(store, pub, now=datetime.fromisoformat(first["scheduled_for"]))
    assert pub.sent == [first["id"]]
    assert store.post(first["id"])["status"] == "posted"
    publish_due(store, pub, now=datetime.fromisoformat(first["scheduled_for"]))
    assert pub.sent == [first["id"]]  # never double-posts


def test_publish_retries_then_fails(store):
    idea_id, _ = launched(store)
    first = store.posts(idea_id=idea_id)[0]
    when = datetime.fromisoformat(first["scheduled_for"])
    for attempt in range(1, MAX_ATTEMPTS + 1):
        publish_due(store, Recorder(fail=True), now=when)
        expected = "failed" if attempt == MAX_ATTEMPTS else "approved"
        assert store.post(first["id"])["status"] == expected


def test_refill_tops_up_only_when_low(store):
    idea_id, _ = launched(store)
    assert refill_content(store, FakeLLM(), PROFILE) == {}       # 3 days queued, need 2
    later = now_utc() + timedelta(days=3)
    added = refill_content(store, FakeLLM(), PROFILE, now=later)
    assert added == {idea_id: 6}
    posts = store.posts(idea_id=idea_id)
    assert len({p["scheduled_for"][:10] for p in posts}) == 6    # new days, no overlap


def test_paused_ideas_are_not_refilled(store):
    idea_id, _ = launched(store)
    store.idea(idea_id)["status"] = "paused"
    assert refill_content(store, FakeLLM(), PROFILE, now=now_utc() + timedelta(days=30)) == {}


def test_landing_page_escapes_html(store):
    idea_id, _ = launched(store)
    kit = {**store.data["launch_kits"][idea_id], "headline": "<script>x</script>"}
    html = render_landing(store.idea(idea_id), kit, "https://formspree.io/f/abc")
    assert "<script>x" not in html and "&lt;script&gt;" in html
    assert 'action="https://formspree.io/f/abc"' in html
