"""The model behind the agent.

`get_llm()` returns Claude when ANTHROPIC_API_KEY is set, otherwise a
deterministic FakeLLM so everything (including the tests) runs offline.
Both expose one method:

    llm.structured(system, prompt, Schema, hints) -> Schema instance
"""

import os
from typing import Protocol, TypeVar

from pydantic import BaseModel

from .schemas import (ContentPlan, IdeaDraft, IdeaList, IdeaScore, LaunchKit,
                      LaunchStep, PostDraft, PostReview, ReviewList, ScoreList)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-sonnet-5-5"


class LLM(Protocol):
    def structured(self, system: str, prompt: str, schema: type[T],
                   hints: dict | None = None) -> T: ...


class ClaudeLLM:
    def __init__(self, model: str | None = None):
        from langchain_anthropic import ChatAnthropic

        self.chat = ChatAnthropic(
            model=model or os.environ.get("GROWTH_AGENT_MODEL", DEFAULT_MODEL),
            max_tokens=16000,
        )

    def structured(self, system, prompt, schema, hints=None):
        return self.chat.with_structured_output(schema).invoke(
            [("system", system), ("human", prompt)])


class FakeLLM:
    """Canned answers shaped like the real thing. `hints` carries the few
    facts the fake needs (how many ideas, which platforms, etc.)."""

    def structured(self, system, prompt, schema, hints=None):
        hints = hints or {}
        if schema is IdeaList:
            n = hints.get("count", 3)
            return IdeaList(ideas=[IdeaDraft(
                name=f"Sample Idea {i + 1}",
                kind="saas" if i % 2 == 0 else "free_platform",
                one_liner="Automated follow-up for local service businesses.",
                target_customer="Local home-service companies with 2-10 staff",
                pain="Leads go cold because nobody follows up within an hour.",
                solution="Instant SMS + email follow-up and a booking link.",
                lead_magnet="Free 'missed revenue' calculator",
                monetization="$49/month per location",
                first_revenue_path="Call 30 local businesses, offer a 2-week pilot.",
                mvp_scope="Web form -> Twilio SMS -> Calendly link.",
                channels=["linkedin", "local facebook groups"],
            ) for i in range(n)])
        if schema is ScoreList:
            return ScoreList(scores=[IdeaScore(
                name=name, pain=8, willingness_to_pay=7 - i % 3, reach=7,
                speed_to_revenue=8, build_ease=6,
                rationale="Clear pain with existing spend; reachable locally.",
            ) for i, name in enumerate(hints.get("names", []))])
        if schema is LaunchKit:
            return LaunchKit(
                headline="Never lose another lead to slow follow-up",
                subheadline="Every new enquiry gets a reply in under 60 seconds.",
                value_props=["Reply instantly, 24/7", "Book jobs automatically",
                             "See exactly which leads converted"],
                lead_magnet_title="The Missed Revenue Calculator",
                lead_magnet_outline=["Enter monthly leads", "See revenue lost",
                                     "Get a 3-step fix"],
                call_to_action="Calculate my missed revenue",
                founding_offer="First 10 customers: $29/month for life, set up for you.",
                launch_plan=[LaunchStep(day=d, task=f"Launch task for day {d}")
                             for d in range(1, 15)],
                outreach_script="Hi {name}, I noticed ... would a free audit help?",
                success_metrics=["100 calculator uses", "20 leads", "3 paying customers"],
            )
        if schema is ContentPlan:
            platforms = hints.get("platforms", ["linkedin"])
            days = hints.get("days", 3)
            angles = ["educate", "story", "lead_magnet", "proof", "offer", "engage"]
            return ContentPlan(posts=[PostDraft(
                platform=p, day_offset=d, hour=9 + i, angle=angles[d % len(angles)],
                text=f"[{p}] Day {d}: tip about following up with leads fast.",
            ) for d in range(days) for i, p in enumerate(platforms)])
        if schema is ReviewList:
            return ReviewList(reviews=[PostReview(index=i, approved=True)
                                       for i in range(hints.get("count", 0))])
        raise ValueError(f"FakeLLM has no canned answer for {schema.__name__}")


def get_llm() -> LLM:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeLLM()
    return FakeLLM()
