"""Diagnostic prior-knowledge assessment and 90-day plan calibration.

Pure functions only (no I/O besides loading/saving JSON) so the scoring logic is
easy to test. The interactive experience lives in ``trainer/cli.py``.

Scoring
-------
* Each question carries a difficulty of 1-3, which is also its weight.
* Domain mastery = weighted share of points earned in that domain.
* Each curriculum module maps to one or more domains; its mastery is the mean.
* Module track:   mastery >= 0.80 -> fast-track  (review + checkpoint)
                  mastery >= 0.50 -> accelerated
                  otherwise       -> full
  Optional modules (M0 Python Basics) are skipped instead of fast-tracked.
* Days are re-allocated so the plan always fills the sprint exactly (90 days by
  default, adjustable with --days): time saved on what you already know is spent
  on what you don't.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

HERE = Path(__file__).parent
BANK_PATH = HERE / "assessment_bank.json"
CURRICULUM_PATH = HERE / "curriculum.json"
PROGRESS_PATH = Path.cwd() / ".trainer_progress.json"

DOMAIN_LABELS = {
    "python_data_structures": "Python data structures",
    "api_mechanics": "API mechanics",
    "validation_schemas": "Validation schemas",
    "orchestration": "Orchestration",
    "data_security": "Data security",
    "tool_calling": "Tool calling",
    "state_persistence": "State & persistence",
    "observability": "Observability",
    "evaluations": "Evaluations",
}

Track = Literal["skip", "fast-track", "accelerated", "full"]
TRACK_WEIGHT: dict[str, float] = {"skip": 0.0, "fast-track": 0.2, "accelerated": 0.6, "full": 1.0}
MIN_MODULE_DAYS = 3
MIN_SPRINT_DAYS, MAX_SPRINT_DAYS = 60, 180

LEVELS = [  # (minimum overall score, level name, description)
    (0.85, "Architect", "Strong fundamentals. Skim early modules and invest in security, evals and shipping."),
    (0.60, "Practitioner", "Solid base with specific gaps. Fast-track what you know; go deep on the weak domains."),
    (0.35, "Builder", "Some foundations in place. Follow the plan closely and do every code-along."),
    (0.00, "Foundation", "Start at the very beginning and take it steadily - every later module builds on the first."),
]


class ModulePlan(BaseModel):
    id: str
    title: str
    mastery: float | None = Field(description="None when the module's domain is not assessed")
    track: Track
    days: int
    start_day: int | None = Field(description="None when the module is skipped")
    end_day: int | None = None


class AssessmentResult(BaseModel):
    taken_at: str
    answers: dict[str, str]
    correct: list[str]
    incorrect: list[str]
    overall: float
    domain_scores: dict[str, float]
    level: str
    level_description: str
    start_module: str
    sprint_days: int = 90
    plan: list[ModulePlan]


def load_bank(path: Path = BANK_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


def load_curriculum(path: Path = CURRICULUM_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def score_domains(questions: list[dict[str, Any]], answers: dict[str, str]) -> tuple[dict[str, float], float]:
    earned: dict[str, float] = {}
    possible: dict[str, float] = {}
    for q in questions:
        weight = float(q["difficulty"])
        possible[q["domain"]] = possible.get(q["domain"], 0.0) + weight
        if answers.get(q["id"], "").strip().lower() == q["answer"]:
            earned[q["domain"]] = earned.get(q["domain"], 0.0) + weight
    domains = {d: round(earned.get(d, 0.0) / p, 4) for d, p in possible.items()}
    overall = round(sum(earned.values()) / sum(possible.values()), 4) if possible else 0.0
    return domains, overall


def level_for(overall: float) -> tuple[str, str]:
    for floor, name, description in LEVELS:
        if overall >= floor:
            return name, description
    return LEVELS[-1][1], LEVELS[-1][2]  # pragma: no cover


def track_for(mastery: float | None, optional: bool = False) -> Track:
    if mastery is None:  # not assessed (e.g. evaluations capstone): everyone does it fully
        return "full"
    if mastery >= 0.8:
        return "skip" if optional else "fast-track"
    if mastery >= 0.5:
        return "accelerated"
    return "full"


def allocate_days(weights: list[float], total: int, minimum: int = MIN_MODULE_DAYS) -> list[int]:
    """Split ``total`` days proportionally to ``weights`` (largest remainder), each >= minimum."""
    n = len(weights)
    spare = total - minimum * n
    if spare < 0:
        raise ValueError("sprint too short for the number of modules")
    if sum(weights) <= 0:
        weights = [1.0] * n
    weight_sum = sum(weights)
    raw = [spare * w / weight_sum for w in weights]
    days = [minimum + int(r) for r in raw]
    leftovers = total - sum(days)
    for i in sorted(range(n), key=lambda i: raw[i] - int(raw[i]), reverse=True)[:leftovers]:
        days[i] += 1
    return days


def build_plan(domain_scores: dict[str, float], curriculum: dict[str, Any],
               sprint_days: int | None = None) -> list[ModulePlan]:
    modules = curriculum["modules"]
    masteries: list[float | None] = []
    for m in modules:
        scores = [domain_scores[d] for d in m["domains"] if d in domain_scores]
        masteries.append(round(sum(scores) / len(scores), 4) if scores else None)
    tracks = [track_for(ms, m.get("optional", False)) for m, ms in zip(modules, masteries)]
    active = [i for i, t in enumerate(tracks) if t != "skip"]
    base = [modules[i]["days"][1] - modules[i]["days"][0] + 1 for i in active]
    allocated = allocate_days([b * TRACK_WEIGHT[tracks[i]] for b, i in zip(base, active)],
                              sprint_days or curriculum["sprint_days"])
    days = dict(zip(active, allocated))

    plan, day = [], 1
    for i, (m, ms, t) in enumerate(zip(modules, masteries, tracks)):
        d = days.get(i, 0)
        plan.append(ModulePlan(id=m["id"], title=m["title"], mastery=ms, track=t, days=d,
                               start_day=day if d else None, end_day=day + d - 1 if d else None))
        day += d
    return plan


def assess(answers: dict[str, str], questions: list[dict[str, Any]] | None = None,
           curriculum: dict[str, Any] | None = None, sprint_days: int | None = None) -> AssessmentResult:
    questions = questions if questions is not None else load_bank()
    curriculum = curriculum if curriculum is not None else load_curriculum()
    sprint_days = sprint_days or curriculum["sprint_days"]
    if not MIN_SPRINT_DAYS <= sprint_days <= MAX_SPRINT_DAYS:
        raise ValueError(f"sprint must be {MIN_SPRINT_DAYS}-{MAX_SPRINT_DAYS} days")
    domain_scores, overall = score_domains(questions, answers)
    level, description = level_for(overall)
    plan = build_plan(domain_scores, curriculum, sprint_days)
    start = next((p.id for p in plan if p.track not in ("skip", "fast-track")), plan[-1].id)
    correct = [q["id"] for q in questions if answers.get(q["id"], "").strip().lower() == q["answer"]]
    return AssessmentResult(
        taken_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        answers=answers, correct=correct,
        incorrect=[q["id"] for q in questions if q["id"] not in correct],
        overall=overall, domain_scores=domain_scores, level=level,
        level_description=description, start_module=start, sprint_days=sprint_days, plan=plan,
    )


# --------------------------------------------------------------------------- persistence


def load_progress(path: Path = PROGRESS_PATH) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"assessment": None, "completed_modules": []}


def save_progress(progress: dict[str, Any], path: Path = PROGRESS_PATH) -> None:
    path.write_text(json.dumps(progress, indent=2), encoding="utf-8")
