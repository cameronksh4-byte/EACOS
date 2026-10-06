"""Structured shapes the LLM must fill in.

Every LLM call in this agent returns one of these Pydantic models (via
`with_structured_output`), so the rest of the code never parses free text.
Field descriptions are sent to the model — they are part of the prompt.
"""

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Brainstorming
# ---------------------------------------------------------------------------
class IdeaDraft(BaseModel):
    name: str = Field(description="Short, memorable product name.")
    kind: Literal["saas", "free_platform"] = Field(
        description="'saas' = paid software product. 'free_platform' = a free "
                    "tool/resource/community that gives businesses real value "
                    "and captures qualified leads for a paid offer.")
    one_liner: str = Field(description="One sentence: who it's for and what it does.")
    target_customer: str = Field(description="A specific business segment, e.g. "
                                             "'independent HVAC contractors with 2-10 techs'.")
    pain: str = Field(description="The painful, expensive problem it solves.")
    solution: str = Field(description="How the product solves it.")
    lead_magnet: str = Field(description="The free thing that attracts leads "
                                         "(calculator, audit, template, directory...).")
    monetization: str = Field(description="How it makes money and at what price.")
    first_revenue_path: str = Field(description="Concrete steps to the first paying "
                                                "customer within 30 days.")
    mvp_scope: str = Field(description="Smallest version that can be shipped in 1-2 weeks.")
    channels: list[str] = Field(description="Where the target customers already hang out.")


class IdeaList(BaseModel):
    ideas: list[IdeaDraft]


class IdeaScore(BaseModel):
    name: str = Field(description="Exact name of the idea being scored.")
    pain: int = Field(ge=1, le=10, description="How urgent and costly the problem is.")
    willingness_to_pay: int = Field(ge=1, le=10, description="Evidence that buyers already pay for this.")
    reach: int = Field(ge=1, le=10, description="How easily the founder can reach these buyers.")
    speed_to_revenue: int = Field(ge=1, le=10, description="How fast the first dollar can arrive.")
    build_ease: int = Field(ge=1, le=10, description="How easy the MVP is for this founder to build.")
    rationale: str = Field(description="Two or three sentences justifying the scores.")


class ScoreList(BaseModel):
    scores: list[IdeaScore]


# ---------------------------------------------------------------------------
# Launching
# ---------------------------------------------------------------------------
class LaunchStep(BaseModel):
    day: int = Field(description="Day number, starting at 1.")
    task: str


class LaunchKit(BaseModel):
    headline: str = Field(description="Landing page headline: outcome-focused, no hype.")
    subheadline: str
    value_props: list[str] = Field(description="Three to five concrete benefits.")
    lead_magnet_title: str
    lead_magnet_outline: list[str] = Field(description="Sections/contents of the lead magnet.")
    call_to_action: str = Field(description="Button text for the lead capture form.")
    founding_offer: str = Field(description="An honest founding-customer offer that "
                                            "drives first revenue (price, what's included).")
    launch_plan: list[LaunchStep] = Field(description="A 14-day plan to first leads and revenue.")
    outreach_script: str = Field(description="A short, personal, non-spammy direct "
                                             "message to a prospective customer.")
    success_metrics: list[str] = Field(description="Numbers that show it's working.")


# ---------------------------------------------------------------------------
# Social content
# ---------------------------------------------------------------------------
class PostDraft(BaseModel):
    platform: str = Field(description="One of the platforms you were given.")
    day_offset: int = Field(ge=0, description="Days after the start date to publish.")
    hour: int = Field(ge=0, le=23, description="Local hour of day to publish.")
    angle: Literal["educate", "story", "proof", "lead_magnet", "offer", "engage"] = Field(
        description="What job the post does in the funnel.")
    text: str = Field(description="The full post text, written natively for the platform.")


class ContentPlan(BaseModel):
    posts: list[PostDraft]


class PostReview(BaseModel):
    index: int = Field(description="Index of the post in the list you were given.")
    approved: bool = Field(description="True if the post is safe and on-brand to publish as-is "
                                       "or after your revision.")
    issues: list[str] = Field(default_factory=list,
                              description="Problems found (false claims, spammy tone, "
                                          "platform rule risks, off-brand).")
    revised_text: str | None = Field(default=None,
                                     description="A fixed version, if the original needed changes.")


class ReviewList(BaseModel):
    reviews: list[PostReview]
