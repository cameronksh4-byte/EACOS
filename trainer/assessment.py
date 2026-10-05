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
* Days are re-allocated so the plan always fills exactly the 90-day sprint:
  time saved on what you already know is spent on what you don't.
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
    "evaluations": "Evaluations",
}

Track = Literal["fast-track", "accelerated", "full"]
TRACK_WEIGHT: dict[str, float] = {"fast-track": 0.2, "accelerated": 0.6, "full": 1.0}
MIN_MODULE_DAYS = 3

LEVELS = [  # (minimum overall score, level name, description)
    (0.85, "Architect", "Strong fundamentals. Skim early modules and invest in security, evals and shipping."),
    (0.60, "Practitioner", "Solid base with specific gaps. Fast-track what you know; go deep on the weak domains."),
    (0.35, "Builder", "Some foundations in place. Follow the plan closely and do every code-along."),
    (0.00, "Foundation", "Start from Module 1 and take it steadily - every later module builds on it."),
]


class ModulePlan(BaseModel):
    id: str
    title: str
    mastery: float | None = Field(description="None when the module's domain is not assessed")
    track: Track
    days: int
    start_day: int
    end_day: int


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


def track_for(mastery: float | None) -> Track:
    if mastery is None:  # not assessed (e.g. evaluations capstone): everyone does it fully
        return "full"
    if mastery >= 0.8:
        return "fast-track"
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


def build_plan(domain_scores: dict[str, float], curriculum: dict[str, Any]) -> list[ModulePlan]:
    modules = curriculum["modules"]
    masteries: list[float | None] = []
    for m in modules:
        scores = [domain_scores[d] for d in m["domains"] if d in domain_scores]
        masteries.append(round(sum(scores) / len(scores), 4) if scores else None)
    tracks = [track_for(ms) for ms in masteries]
    base = [m["days"][1] - m["days"][0] + 1 for m in modules]
    days = allocate_days([b * TRACK_WEIGHT[t] for b, t in zip(base, tracks)], curriculum["sprint_days"])

    plan, day = [], 1
    for m, ms, t, d in zip(modules, masteries, tracks, days):
        plan.append(ModulePlan(id=m["id"], title=m["title"], mastery=ms, track=t,
                               days=d, start_day=day, end_day=day + d - 1))
        day += d
    return plan


def assess(answers: dict[str, str], questions: list[dict[str, Any]] | None = None,
           curriculum: dict[str, Any] | None = None) -> AssessmentResult:
    questions = questions if questions is not None else load_bank()
    curriculum = curriculum if curriculum is not None else load_curriculum()
    domain_scores, overall = score_domains(questions, answers)
    level, description = level_for(overall)
    plan = build_plan(domain_scores, curriculum)
    start = next((p.id for p in plan if p.track != "fast-track"), plan[-1].id)
    correct = [q["id"] for q in questions if answers.get(q["id"], "").strip().lower() == q["answer"]]
    return AssessmentResult(
        taken_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        answers=answers, correct=correct,
        incorrect=[q["id"] for q in questions if q["id"] not in correct],
        overall=overall, domain_scores=domain_scores, level=level,
        level_description=description, start_module=start, plan=plan,
    )


# --------------------------------------------------------------------------- persistence


def load_progress(path: Path = PROGRESS_PATH) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"assessment": None, "completed_modules": []}


def save_progress(progress: dict[str, Any], path: Path = PROGRESS_PATH) -> None:
    path.write_text(json.dumps(progress, indent=2), encoding="utf-8")
