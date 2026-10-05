"""Evaluate the resolver agent: Pass@k, schema compliance, tool selection, safety.

Each case in agent_cases.json names a golden document, the acceptable verdicts, the
tools a competent agent must call, and a recorded reference run.

    uv run python -m auditgate.evals.agent_runner                          # replay reference runs (offline)
    uv run python -m auditgate.evals.agent_runner --provider anthropic -n 3 -k 1 --report after.json
    uv run python -m auditgate.evals.agent_runner --provider anthropic --baseline before.json

`replay` checks the harness, tools and policy deterministically (it must score 100%).
A real provider is scored against the same expectations. Run it before and after
every prompt or model change and compare with --baseline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from auditgate.agent.models import Model, ModelTurn, ScriptedModel, ToolCall
from auditgate.agent.resolver import build_model, resolve
from auditgate.config import Settings
from auditgate.evals.metrics import mean, pass_at_k, precision_recall
from auditgate.evals.runner import load_cases
from auditgate.extraction.schemas import DocumentType
from auditgate.pipeline import AuditReport, process_text
from auditgate.security.sanitizer import Sanitizer

AGENT_CASES = Path(__file__).with_name("agent_cases.json")
GATES = {"pass_at_1": 0.75, "schema_compliance": 0.80, "tool_recall": 0.80, "tool_precision": 0.70}
REGRESSION_TOLERANCE = 0.05


class Attempt(BaseModel):
    success: bool
    verdict: str | None
    disputed_amount: str | None
    tools_called: list[str]
    submits: int
    invalid_submits: int
    steps: int
    failure: str | None = None


class CaseScore(BaseModel):
    id: str
    attempts: list[Attempt]
    pass_at_1: float
    pass_at_k: float
    tool_precision: float
    tool_recall: float


class AgentEvalReport(BaseModel):
    provider: str
    n: int
    k: int
    cases: list[CaseScore]
    metrics: dict[str, float]
    passed: bool
    failed_gates: list[str]


def load_agent_cases(path: Path = AGENT_CASES) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def replay_model(case: dict[str, Any]) -> Model:
    turns = [ModelTurn(text=t["text"], tool_calls=[ToolCall(**c) for c in t["tool_calls"]])
             for t in case["reference_turns"]]
    return ScriptedModel(turns, name="replay")


def _report(doc_case: dict[str, Any]) -> AuditReport:
    return process_text(doc_case["text"], DocumentType(doc_case["doc_type"]), settings=Settings(provider="heuristic"),
                        sanitizer=Sanitizer(spacy_model=None))


def score_attempt(case: dict[str, Any], result) -> Attempt:
    exp = case["expected"]
    calls = [c for step in result.run.steps for c in step.tool_calls]
    submit_results = [r for step in result.run.steps for r in step.results if r.name == "submit_resolution"]
    tools = sorted({c.name for c in calls if c.name != "submit_resolution"})
    verdict = result.resolution.verdict.value if result.resolution else None
    amount = str(result.resolution.disputed_amount) if result.resolution else None

    failure = None
    if result.resolution is None:
        failure = "no valid resolution"
    elif verdict in exp["forbidden_verdicts"]:
        failure = f"forbidden verdict {verdict}"
    elif verdict not in exp["verdicts"]:
        failure = f"verdict {verdict}, expected one of {exp['verdicts']}"
    elif exp["disputed_amount"] is not None and Decimal(amount) != Decimal(exp["disputed_amount"]):
        failure = f"disputed {amount}, expected {exp['disputed_amount']}"
    return Attempt(success=failure is None, verdict=verdict, disputed_amount=amount, tools_called=tools,
                   submits=len(submit_results), invalid_submits=sum(r.is_error for r in submit_results),
                   steps=len(result.run.steps), failure=failure)


def run_agent_evals(model_factory: Callable[[dict[str, Any]], Model], *, provider: str = "replay",
                    n: int = 1, k: int = 1, cases: list[dict[str, Any]] | None = None,
                    max_steps: int = 8) -> AgentEvalReport:
    if not 1 <= k <= n:
        raise ValueError("need 1 <= k <= n")
    docs = {c["id"]: c for c in load_cases()}
    scores: list[CaseScore] = []
    for case in cases if cases is not None else load_agent_cases():
        report = _report(docs[case["doc_case"]])
        attempts = [score_attempt(case, resolve(report, model_factory(case), max_steps=max_steps))
                    for _ in range(n)]
        c = sum(a.success for a in attempts)
        pr = [precision_recall(a.tools_called, case["expected"]["required_tools"],
                               case["expected"]["optional_tools"]) for a in attempts]
        scores.append(CaseScore(id=case["id"], attempts=attempts, pass_at_1=pass_at_k(n, c, 1),
                                pass_at_k=pass_at_k(n, c, k), tool_precision=mean([p for p, _ in pr]),
                                tool_recall=mean([r for _, r in pr])))

    all_attempts = [a for s in scores for a in s.attempts]
    submits = sum(a.submits for a in all_attempts)
    metrics = {
        "pass_at_1": mean([s.pass_at_1 for s in scores]),
        f"pass_at_{k}": mean([s.pass_at_k for s in scores]),
        "schema_compliance": round(1 - sum(a.invalid_submits for a in all_attempts) / submits, 4) if submits else 0.0,
        "tool_precision": mean([s.tool_precision for s in scores]),
        "tool_recall": mean([s.tool_recall for s in scores]),
        "forbidden_verdicts": float(sum(1 for a in all_attempts if a.failure and a.failure.startswith("forbidden"))),
        "avg_steps": mean([a.steps for a in all_attempts]),
    }
    gates = {name: (1.0 if provider == "replay" else floor) for name, floor in GATES.items()}
    failed = [name for name, floor in gates.items() if metrics[name] < floor]
    if metrics["forbidden_verdicts"]:
        failed.append("forbidden_verdicts")
    return AgentEvalReport(provider=provider, n=n, k=k, cases=scores, metrics=metrics,
                           passed=not failed, failed_gates=failed)


def compare(before: dict[str, float], after: dict[str, float]) -> tuple[list[tuple[str, float, float]], list[str]]:
    """Rows of (metric, before, after) and the list of regressions beyond tolerance."""
    rows, regressions = [], []
    for name in after:
        if name not in before:
            continue
        rows.append((name, before[name], after[name]))
        lower_is_better = name in ("forbidden_verdicts", "avg_steps")
        worse = (after[name] - before[name]) if lower_is_better else (before[name] - after[name])
        if worse > (0 if name == "forbidden_verdicts" else REGRESSION_TOLERANCE) and name != "avg_steps":
            regressions.append(name)
    return rows, regressions


def render(report: AgentEvalReport, console: Console) -> None:
    table = Table(title=f"Agent eval - {report.provider} (n={report.n}, k={report.k})")
    for col in ("case", f"pass@1", f"pass@{report.k}", "tools P/R", "verdicts", "failure"):
        table.add_column(col)
    for s in report.cases:
        colour = "green" if s.pass_at_1 == 1 else "yellow" if s.pass_at_1 > 0 else "red"
        table.add_row(s.id, f"[{colour}]{s.pass_at_1:.2f}[/]", f"{s.pass_at_k:.2f}",
                      f"{s.tool_precision:.2f}/{s.tool_recall:.2f}",
                      ", ".join(str(a.verdict) for a in s.attempts),
                      next((a.failure for a in s.attempts if a.failure), "") or "")
    console.print(table)
    for name, value in report.metrics.items():
        console.print(f"  {name:<20} {value:.4g}")
    verdict = "[bold green]PASSED[/]" if report.passed else f"[bold red]FAILED[/] ({', '.join(report.failed_gates)})"
    console.print(f"Result: {verdict}")


def main(
    provider: str = typer.Option("replay", help="replay | local | openai | anthropic"),
    n: int = typer.Option(1, min=1, help="Attempts per case"),
    k: int = typer.Option(1, min=1, help="k for Pass@k (k <= n)"),
    report_path: Path = typer.Option(None, "--report", help="Write the JSON report here"),
    baseline: Path = typer.Option(None, help="Compare against an earlier --report file"),
) -> None:
    if provider == "replay":
        factory = replay_model
    else:
        settings = Settings.from_env().model_copy(update={"provider": provider})
        factory = lambda case: build_model(settings)  # noqa: E731
    report = run_agent_evals(factory, provider=provider, n=n, k=k)
    console = Console()
    render(report, console)
    if report_path:
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    regressions: list[str] = []
    if baseline:
        before = json.loads(baseline.read_text(encoding="utf-8"))["metrics"]
        rows, regressions = compare(before, report.metrics)
        table = Table(title=f"Before vs after ({baseline.name})")
        for col in ("metric", "before", "after", "change"):
            table.add_column(col)
        for name, b, a in rows:
            style = "red" if name in regressions else "green" if a != b else "dim"
            table.add_row(name, f"{b:.4g}", f"{a:.4g}", f"[{style}]{a - b:+.4g}[/]")
        console.print(table)
        if regressions:
            console.print(f"[bold red]Regressions:[/] {', '.join(regressions)}")
    raise typer.Exit(code=0 if report.passed and not regressions else 1)


if __name__ == "__main__":
    typer.run(main)
