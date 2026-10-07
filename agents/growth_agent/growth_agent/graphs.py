"""The two LangGraph workflows that make up the agent.

BRAINSTORM                                  LAUNCH (also used to refill content)

 START                                       START
   │                                           │ has a launch kit already?
   ▼                                        no ├──────────────► build_launch_kit
 generate_ideas   (Claude: N ideas)            │ yes                   │
   │                                           ▼                       │
   ▼                                        plan_content ◄────────────┘
 score_ideas      (Claude: score 5 axes)       │  (Claude: platform-native posts)
   │                                           ▼
   ▼                                        review_content  (Claude: brand-safety pass)
 save_ideas       (weighted rank -> store)     │
   │                                           ▼
  END                                       queue_posts     (schedule -> store)
                                               │
                                              END
"""

from datetime import date, datetime, time, timedelta
from typing import TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from . import prompts
from .llm import LLM
from .schemas import ContentPlan, IdeaList, LaunchKit, ReviewList, ScoreList
from .store import Store, now_utc

DEFAULT_WEIGHTS = {"pain": 1.2, "willingness_to_pay": 1.5, "reach": 1.2,
                   "speed_to_revenue": 1.5, "build_ease": 1.0}


# ---------------------------------------------------------------------------
# BRAINSTORM
# ---------------------------------------------------------------------------
class BrainstormState(TypedDict, total=False):
    profile: dict
    count: int
    focus: str
    drafts: list[dict]
    scores: list[dict]
    saved_ids: list[str]


def build_brainstorm_graph(llm: LLM, store: Store):
    def generate_ideas(state: BrainstormState) -> dict:
        existing = [i["name"] for i in store.ideas()]
        prompt = (
            f"{prompts.profile_block(state['profile'])}\n"
            f"Generate {state['count']} distinct business ideas for this founder: a mix of "
            "paid SaaS products and FREE value platforms (free tools, audits, calculators, "
            "directories, communities, templates) that win qualified business leads and "
            "lead to a paid offer.\n"
            f"Focus: {state.get('focus') or 'whatever best fits the founder'}\n"
            f"Avoid repeating these existing ideas: {existing or 'none'}"
        )
        result = llm.structured(prompts.STRATEGIST, prompt, IdeaList,
                                hints={"count": state["count"]})
        return {"drafts": [i.model_dump() for i in result.ideas]}

    def score_ideas(state: BrainstormState) -> dict:
        names = [d["name"] for d in state["drafts"]]
        prompt = (
            f"{prompts.profile_block(state['profile'])}\n"
            "Score each idea 1-10 on pain, willingness_to_pay, reach, speed_to_revenue and "
            "build_ease FOR THIS FOUNDER. Be critical; most ideas should not score 9-10.\n\n"
            + "\n\n".join(f"- {d['name']}: {d['one_liner']}\n  customer: {d['target_customer']}"
                          f"\n  money: {d['monetization']}\n  mvp: {d['mvp_scope']}"
                          for d in state["drafts"])
        )
        result = llm.structured(prompts.STRATEGIST, prompt, ScoreList, hints={"names": names})
        return {"scores": [s.model_dump() for s in result.scores]}

    def save_ideas(state: BrainstormState) -> dict:
        weights = {**DEFAULT_WEIGHTS, **state["profile"].get("scoring", {}).get("weights", {})}
        max_total = 10 * sum(weights.values())
        by_name = {s["name"]: s for s in state["scores"]}
        ids = []
        for draft in state["drafts"]:
            score = dict(by_name.get(draft["name"], {}))
            score.pop("name", None)
            raw = sum(score.get(k, 0) * w for k, w in weights.items())
            score["total"] = round(100 * raw / max_total)
            ids.append(store.add_idea({**draft, "score": score}))
        store.save()
        return {"saved_ids": ids}

    g = StateGraph(BrainstormState)
    g.add_node("generate_ideas", generate_ideas)
    g.add_node("score_ideas", score_ideas)
    g.add_node("save_ideas", save_ideas)
    g.add_edge(START, "generate_ideas")
    g.add_edge("generate_ideas", "score_ideas")
    g.add_edge("score_ideas", "save_ideas")
    g.add_edge("save_ideas", END)
    return g.compile()


# ---------------------------------------------------------------------------
# LAUNCH  /  CONTENT REFILL
# ---------------------------------------------------------------------------
class LaunchState(TypedDict, total=False):
    profile: dict
    idea_id: str
    rebuild_kit: bool
    days: int
    drafts: list[dict]
    reviews: list[dict]
    queued_ids: list[str]


def _link_for(profile: dict, idea: dict) -> str | None:
    if idea.get("landing_url"):
        return idea["landing_url"]
    template = profile.get("social", {}).get("link_template")
    return template.format(idea_id=idea["id"]) if template else None


def _start_date(store: Store, idea_id: str, tz: ZoneInfo) -> date:
    """Day after the last post already scheduled for this idea (or tomorrow)."""
    tomorrow = now_utc().astimezone(tz).date() + timedelta(days=1)
    scheduled = [p for p in store.posts(idea_id=idea_id) if p["status"] != "rejected"]
    if not scheduled:
        return tomorrow
    last = datetime.fromisoformat(scheduled[-1]["scheduled_for"]).astimezone(tz).date()
    return max(tomorrow, last + timedelta(days=1))


def build_launch_graph(llm: LLM, store: Store):
    def build_launch_kit(state: LaunchState) -> dict:
        idea = store.idea(state["idea_id"])
        prompt = (
            f"{prompts.profile_block(state['profile'])}\n"
            "Create a launch kit that gets this idea its first qualified leads and first "
            "paying customer within 14 days.\n\nIDEA\n"
            + "\n".join(f"{k}: {v}" for k, v in idea.items() if k not in ("score", "id"))
        )
        kit = llm.structured(prompts.STRATEGIST, prompt, LaunchKit)
        store.data["launch_kits"][idea["id"]] = kit.model_dump()
        idea["status"] = "launched"
        idea["launched_at"] = now_utc().isoformat()
        store.save()
        return {}

    def plan_content(state: LaunchState) -> dict:
        idea = store.idea(state["idea_id"])
        kit = store.data["launch_kits"][idea["id"]]
        social = state["profile"].get("social", {})
        platforms = social.get("platforms", ["linkedin"])
        days = state.get("days") or social.get("plan_days", 7)
        per_day = social.get("posts_per_day_per_platform", 1)
        recent = [p["text"] for p in store.posts(idea_id=idea["id"])][-10:]
        prompt = (
            f"{prompts.profile_block(state['profile'])}\n"
            f"Write a {days}-day social content plan for: {idea['name']} - {idea['one_liner']}\n"
            f"Customer: {idea['target_customer']}\nPain: {idea['pain']}\n"
            f"Lead magnet: {kit['lead_magnet_title']}\nCTA: {kit['call_to_action']}\n"
            f"Founding offer: {kit['founding_offer']}\n"
            f"Link to use: {_link_for(state['profile'], idea) or '(no link yet - do not invent one)'}\n"
            f"Platforms: {platforms}. {per_day} post(s) per platform per day, "
            f"day_offset 0..{days - 1}.\n"
            "Mix angles roughly 40% educate, 20% story, 20% lead_magnet, 10% proof, "
            "10% offer/engage. Each post must stand alone.\n"
            f"Don't repeat these recent posts: {recent or 'none'}"
        )
        plan = llm.structured(prompts.COPYWRITER, prompt, ContentPlan,
                              hints={"platforms": platforms, "days": days})
        drafts = [p.model_dump() for p in plan.posts if p.platform in platforms]
        return {"drafts": drafts}

    def review_content(state: LaunchState) -> dict:
        drafts = state["drafts"]
        if not drafts:
            return {"reviews": []}
        prompt = (
            f"{prompts.profile_block(state['profile'])}\n"
            "Review every post below. Return one review per index.\n\n"
            + "\n\n".join(f"[{i}] ({d['platform']}) {d['text']}" for i, d in enumerate(drafts))
        )
        result = llm.structured(prompts.REVIEWER, prompt, ReviewList,
                                hints={"count": len(drafts)})
        return {"reviews": [r.model_dump() for r in result.reviews]}

    def queue_posts(state: LaunchState) -> dict:
        social = state["profile"].get("social", {})
        tz = ZoneInfo(social.get("timezone", "UTC"))
        auto = social.get("approval", "auto") == "auto"
        reviews = {r["index"]: r for r in state["reviews"]}
        start = _start_date(store, state["idea_id"], tz)
        ids = []
        for i, d in enumerate(state["drafts"]):
            # A post the reviewer never looked at is treated as rejected.
            review = reviews.get(i, {"approved": False, "issues": ["not reviewed"]})
            when = datetime.combine(start + timedelta(days=d["day_offset"]),
                                    time(d["hour"]), tz)
            status = ("approved" if auto else "pending") if review["approved"] else "rejected"
            ids.append(store.add_post({
                "idea_id": state["idea_id"],
                "platform": d["platform"],
                "angle": d["angle"],
                "text": review.get("revised_text") or d["text"],
                "scheduled_for": when.isoformat(),
                "status": status,              # pending -> approved -> posted | failed
                "review_issues": review.get("issues", []),
                "attempts": 0,
            }))
        store.save()
        return {"queued_ids": ids}

    def route_start(state: LaunchState) -> str:
        has_kit = state["idea_id"] in store.data["launch_kits"]
        return "plan_content" if has_kit and not state.get("rebuild_kit") else "build_launch_kit"

    g = StateGraph(LaunchState)
    g.add_node("build_launch_kit", build_launch_kit)
    g.add_node("plan_content", plan_content)
    g.add_node("review_content", review_content)
    g.add_node("queue_posts", queue_posts)
    g.add_conditional_edges(START, route_start, ["build_launch_kit", "plan_content"])
    g.add_edge("build_launch_kit", "plan_content")
    g.add_edge("plan_content", "review_content")
    g.add_edge("review_content", "queue_posts")
    g.add_edge("queue_posts", END)
    return g.compile()
