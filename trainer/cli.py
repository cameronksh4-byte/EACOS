"""AuditGate Trainer - a code-along CLI for the 90-day AI engineering sprint.

    uv run python -m trainer.cli assess         # diagnostic, calibrates your plan
    uv run python -m trainer.cli plan           # your personalised 90-day schedule
    uv run python -m trainer.cli modules        # curriculum overview
    uv run python -m trainer.cli module M0      # objectives + code-along steps
    uv run python -m trainer.cli check M3       # run the module checkpoint tests
    uv run python -m trainer.cli eval           # run the AuditGate eval gate
    uv run python -m trainer.cli status         # where you are and what's next
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.table import Table

from trainer.assessment import (
    DOMAIN_LABELS,
    AssessmentResult,
    assess as run_assessment,
    load_bank,
    load_curriculum,
    load_progress,
    save_progress,
)

app = typer.Typer(add_completion=False, help="AuditGate Trainer: assess, learn, build, evaluate.")
console = Console()

TRACK_STYLE = {"skip": "dim", "fast-track": "green", "accelerated": "yellow", "full": "red"}


def _bar(score: float, width: int = 20) -> str:
    filled = round(score * width)
    colour = "green" if score >= 0.8 else "yellow" if score >= 0.5 else "red"
    return f"[{colour}]{'█' * filled}[/][dim]{'░' * (width - filled)}[/] {score:.0%}"


def _module(module_id: str) -> dict[str, Any]:
    for m in load_curriculum()["modules"]:
        if m["id"].lower() == module_id.lower():
            return m
    console.print(f"[red]Unknown module {module_id!r}.[/] Try: uv run python -m trainer.cli modules")
    raise typer.Exit(code=2)


def _parse_answers(raw: str, questions: list[dict[str, Any]]) -> dict[str, str]:
    """Accept 'PY1=a,PY2=b' or a positional list 'a,b,c,...' (in bank order)."""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if all("=" in p for p in parts):
        return {k.strip().upper(): v.strip().lower() for k, v in (p.split("=", 1) for p in parts)}
    return {q["id"]: a.lower() for q, a in zip(questions, parts)}


def _ask(questions: list[dict[str, Any]]) -> dict[str, str]:
    console.print(Panel.fit(
        f"{len(questions)} questions across {len({q['domain'] for q in questions})} domains. "
        "Answer honestly - press [bold]s[/] to skip anything you\n"
        "don't know. Guessing inflates your score and skips lessons you actually need.",
        title="Diagnostic Prior Knowledge Assessment", border_style="cyan"))
    answers: dict[str, str] = {}
    for i, q in enumerate(questions, start=1):
        label = DOMAIN_LABELS.get(q["domain"], q["domain"])
        console.rule(f"[bold]Q{i}/{len(questions)}[/] · {label} · difficulty {'★' * q['difficulty']}")
        console.print(q["prompt"])
        if q.get("code"):
            console.print(Syntax(q["code"], "python", theme="monokai", line_numbers=False))
        for key, text in q["options"].items():
            console.print(f"  [cyan]{key})[/] {text}")
        answer = Prompt.ask("Your answer", choices=[*q["options"], "s"], show_choices=True)
        answers[q["id"]] = "" if answer == "s" else answer
    return answers


def _show_result(result: AssessmentResult, questions: list[dict[str, Any]]) -> None:
    console.print(Panel.fit(
        f"[bold]{result.level}[/] - overall {result.overall:.0%}\n{result.level_description}\n\n"
        f"Start at: [bold cyan]{result.start_module}[/]",
        title="Your baseline", border_style="magenta"))

    domains = Table(title="Domain mastery", show_header=True)
    domains.add_column("Domain")
    domains.add_column("Score")
    for domain, score in sorted(result.domain_scores.items(), key=lambda kv: kv[1]):
        domains.add_row(DOMAIN_LABELS.get(domain, domain), _bar(score))
    console.print(domains)
    _show_plan(result)

    missed = [q for q in questions if q["id"] in result.incorrect]
    if missed:
        console.rule("Review these before you start")
        for q in missed:
            given = result.answers.get(q["id"]) or "skipped"
            console.print(f"[bold]{q['id']}[/] (you: {given}, answer: [green]{q['answer']}[/]) {q['explanation']}\n")


def _show_plan(result: AssessmentResult) -> None:
    table = Table(title=f"Your calibrated {result.sprint_days}-day sprint")
    for col in ("Module", "Title", "Mastery", "Track", "Days", "Schedule"):
        table.add_column(col)
    for p in result.plan:
        mastery = "n/a (capstone)" if p.mastery is None else f"{p.mastery:.0%}"
        style = TRACK_STYLE[p.track]
        marker = " ◀ start" if p.id == result.start_module else ""
        schedule = f"Day {p.start_day}-{p.end_day}" if p.days else "[dim]skipped - you know this[/]"
        table.add_row(p.id + marker, p.title, mastery, f"[{style}]{p.track}[/]", str(p.days), schedule)
    console.print(table)


@app.command()
def assess(
    answers: str = typer.Option(None, help="Non-interactive: 'PY1=a,PY2=b,...' or 'a,b,c,...' in bank order"),
    save: bool = typer.Option(True, help="Save the result to .trainer_progress.json"),
    days: int = typer.Option(90, min=60, max=180, help="Sprint length in days (e.g. 120 for a steadier pace)"),
) -> None:
    """Take the diagnostic and calibrate your starting point."""
    questions = load_bank()
    given = _parse_answers(answers, questions) if answers else _ask(questions)
    result = run_assessment(given, questions, sprint_days=days)
    _show_result(result, questions)
    if save:
        progress = load_progress()
        progress["assessment"] = result.model_dump()
        save_progress(progress)
        console.print("[dim]Saved to .trainer_progress.json - run `uv run python -m trainer.cli plan` anytime.[/]")


@app.command()
def plan() -> None:
    """Show your personalised 90-day plan."""
    progress = load_progress()
    if not progress.get("assessment"):
        console.print("No assessment yet. Run: [bold]uv run python -m trainer.cli assess[/]")
        raise typer.Exit(code=1)
    _show_plan(AssessmentResult.model_validate(progress["assessment"]))


@app.command()
def modules() -> None:
    """List the curriculum modules."""
    completed = set(load_progress().get("completed_modules", []))
    table = Table(title="Curriculum")
    for col in ("", "Module", "Title", "Default days", "Built", "AuditGate milestone"):
        table.add_column(col)
    status_style = {"ready": "[green]ready[/]", "partial": "[yellow]partial[/]", "planned": "[dim]planned[/]"}
    for m in load_curriculum()["modules"]:
        table.add_row("✅" if m["id"] in completed else "·", m["id"], m["title"],
                      f"{m['days'][0]}-{m['days'][1]}", status_style.get(m.get("build_status", "ready"), ""),
                      m["milestone"])
    console.print(table)


@app.command()
def module(module_id: str) -> None:
    """Show objectives, concepts and code-along steps for a module."""
    m = _module(module_id)
    console.print(Panel(f"{m['why']}", title=f"{m['id']} · {m['title']}", border_style="cyan"))
    if m.get("build_status") != "ready":
        console.print(f"[yellow]Build status: {m.get('build_status')}[/] - steps marked [coming in a later stage] "
                      "aren't in the codebase yet. Everything else is ready to work on.\n")
    console.print("[bold]Objectives[/]")
    for o in m["objectives"]:
        console.print(f"  • {o}")
    console.print("\n[bold]Key concepts[/]: " + ", ".join(m["concepts"]))
    console.rule("Code-along")
    for i, step in enumerate(m["code_along"], start=1):
        console.print(f"[bold cyan]{i}. {step['title']}[/]  [dim]{step['file']}[/]\n   {step['task']}\n")
    console.print(f"[bold]Checkpoint[/]: [green]{m['checkpoint']}[/]   (or: uv run python -m trainer.cli check {m['id']})")
    console.print(f"[bold]Milestone[/]: {m['milestone']}")
    console.print(f"[bold]Stretch[/]: {m['stretch']}")


@app.command()
def check(module_id: str) -> None:
    """Run a module's checkpoint tests and record completion if they pass."""
    m = _module(module_id)
    if m.get("build_status") == "planned":
        console.print(f"[yellow]{m['id']} isn't built yet[/] - its checkpoint tests arrive with its code. "
                      "Work on the modules before it for now.")
        raise typer.Exit(code=1)
    cmd = shlex.split(m["checkpoint"])
    if cmd[:2] == ["uv", "run"]:  # already inside the project env: call pytest directly
        cmd = [sys.executable, "-m", *cmd[2:]]
    console.print(f"[dim]$ {' '.join(cmd)}[/]")
    code = subprocess.call(cmd)
    if code == 0:
        progress = load_progress()
        done = set(progress.get("completed_modules", [])) | {m["id"]}
        progress["completed_modules"] = sorted(done)
        save_progress(progress)
        console.print(f"[bold green]{m['id']} checkpoint passed.[/] Milestone: {m['milestone']}")
    else:
        console.print(f"[bold yellow]{m['id']}: not there yet.[/] Each failure above is one thing left to do - "
                      "fix them one at a time and re-run this command.")
    raise typer.Exit(code=code)


@app.command("eval")
def eval_cmd(
    suite: str = typer.Option("all", help="docs | agent | all"),
    provider: str = typer.Option(None, help="Document extractor (heuristic|local|openai|anthropic) and/or agent "
                                            "model (replay|local|openai|anthropic). Defaults are offline."),
) -> None:
    """Run the AuditGate evaluation gates: document extraction and/or the resolver agent."""
    code = 0
    if suite in ("docs", "all"):
        doc_provider = provider if provider not in (None, "replay") else "heuristic"
        code |= subprocess.call([sys.executable, "-m", "auditgate.evals.runner", "--dataset", "all",
                                 "--provider", doc_provider])
    if suite in ("agent", "all"):
        agent_provider = provider if provider not in (None, "heuristic") else "replay"
        code |= subprocess.call([sys.executable, "-m", "auditgate.evals.agent_runner", "--provider", agent_provider])
    raise typer.Exit(code=code)


@app.command()
def status() -> None:
    """Summarise progress and suggest the next step."""
    progress = load_progress()
    if not progress.get("assessment"):
        console.print("Step 1: [bold]uv run python -m trainer.cli assess[/]")
        return
    result = AssessmentResult.model_validate(progress["assessment"])
    done = set(progress.get("completed_modules", []))
    console.print(f"Level: [bold]{result.level}[/] ({result.overall:.0%}) · assessed {result.taken_at}")
    console.print(f"Completed: {', '.join(sorted(done)) or 'none yet'}")
    upcoming = [p for p in result.plan if p.id not in done and p.track != "skip"]
    if upcoming:
        nxt = upcoming[0]
        console.print(f"Next: [bold cyan]{nxt.id} · {nxt.title}[/] ({nxt.track}, {nxt.days} days) -> "
                      f"uv run python -m trainer.cli module {nxt.id}")
    else:
        console.print("[bold green]All modules complete. Time to book demos.[/]")


if __name__ == "__main__":
    app()
