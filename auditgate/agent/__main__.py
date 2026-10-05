"""Run the exception-resolution agent on a golden-dataset case and show every step.

    uv run python -m auditgate.agent --case inv_line_math_error                 # offline scripted demo
    uv run python -m auditgate.agent --case inv_total_inflated --provider anthropic
    uv run python -m auditgate.agent --case bid_total_mismatch --provider local

The scripted provider is NOT an AI: it replays a fixed sequence of tool calls
(including one deliberate mistake) so you can watch the loop, the error feedback
and the self-correction without an API key.
"""

from __future__ import annotations

import json
from decimal import Decimal

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from auditgate.agent.models import ModelTurn, ScriptedModel, ToolCall
from auditgate.agent.resolver import CENT, Verdict, build_model, resolve
from auditgate.config import Settings
from auditgate.evals.runner import load_cases
from auditgate.extraction.schemas import DocumentType
from auditgate.pipeline import AuditReport, process_text

console = Console()


def demo_script(report: AuditReport) -> ScriptedModel:
    """A fixed, readable run for line-math invoices. Stands in for a real model."""
    data = report.data or {}
    vendor = data.get("vendor_name") or data.get("bidder_name") or "unknown"
    bad = next((li for li in data.get("line_items", [])
                if (Decimal(str(li["quantity"])) * Decimal(str(li["unit_price"]))).quantize(CENT)
                != Decimal(str(li["amount"]))), None)
    if bad is None:
        return ScriptedModel([
            ModelTurn(text="Looking up the vendor.", tool_calls=[
                ToolCall("c1", "lookup_vendor_history", {"vendor_name": vendor})]),
            ModelTurn(text="Not enough evidence for a scripted decision.", tool_calls=[
                ToolCall("c2", "submit_resolution", {
                    "verdict": "escalate", "evidence": [f"Findings: {[f.code for f in report.findings]}"],
                    "summary": "The scripted demo only resolves line-math errors; a human should review this."})]),
        ], name="scripted-demo")

    correct = (Decimal(str(bad["quantity"])) * Decimal(str(bad["unit_price"]))).quantize(CENT)
    overcharge = Decimal(str(bad["amount"])) - correct
    return ScriptedModel([
        ModelTurn(text="I'll check this vendor's history and purchase orders first.", tool_calls=[
            ToolCall("c1", "lookup_vendor_history", {"vendor_name": vendor}),
            ToolCall("c2", "find_purchase_orders", {"vendor_name": vendor})]),
        ModelTurn(text=f"Recomputing '{bad['description']}' with the calculator.", tool_calls=[
            ToolCall("c3", "line_total", {"quantity": bad["quantity"], "unit_price": bad["unit_price"]}),
            ToolCall("c4", "overcharge", {"billed": bad["amount"], "correct": str(correct)})]),
        ModelTurn(text="Submitting my resolution.", tool_calls=[  # deliberate mistake: amount as words
            ToolCall("c5", "submit_resolution", {
                "verdict": "dispute", "disputed_amount": f"about {overcharge} dollars",
                "summary": "Line item is overbilled.", "evidence": ["calculator"]})]),
        ModelTurn(text="Fixing the fields the tool rejected.", tool_calls=[
            ToolCall("c6", "submit_resolution", {
                "verdict": "dispute", "disputed_amount": str(overcharge),
                "summary": (f"'{bad['description']}' is billed at {bad['amount']} but {bad['quantity']} x "
                            f"{bad['unit_price']} = {correct}. We should dispute the {overcharge} difference."),
                "evidence": [f"line_total returned {correct}", f"overcharge returned {overcharge}"],
                "draft_email": (f"Hello {vendor},\n\nThank you for invoice {data.get('invoice_number')}. "
                                f"The line '{bad['description']}' shows {bad['amount']}, but {bad['quantity']} x "
                                f"{bad['unit_price']} comes to {correct}. Could you send a corrected invoice "
                                f"reducing the total by {overcharge}?\n\nThank you.")})]),
    ], name="scripted-demo")


def render(result, report: AuditReport) -> None:
    findings = ", ".join(f.code for f in report.findings) or "none"
    console.print(Panel(f"Document status: [bold]{report.status.value}[/]  ·  findings: {findings}",
                        title="Input", border_style="cyan"))
    run = result.run
    for step in run.steps:
        table = Table(show_header=True, title=f"Step {step.index}  ·  {step.latency_ms:.0f} ms", title_justify="left")
        table.add_column("tool")
        table.add_column("arguments (as the model sent them)")
        table.add_column("result (as the model saw it)")
        for call, res in zip(step.tool_calls, step.results):
            colour = "red" if res.is_error else "green"
            table.add_row(call.name, json.dumps(call.arguments, default=str)[:70], f"[{colour}]{res.content[:160]}[/]")
        if step.text:
            console.print(f"[italic]model: {step.text}[/]")
        console.print(table)
    tin, tout = run.tokens
    console.print(f"[dim]{len(run.steps)} steps · {run.repairs} self-corrections · tokens in/out {tin}/{tout}[/]")

    if result.resolution is None:
        console.print(Panel("\n".join(result.policy_violations), title="Escalated to a human", border_style="red"))
        return
    r = result.resolution
    colour = {Verdict.APPROVE: "green", Verdict.DISPUTE: "yellow", Verdict.ESCALATE: "red"}[r.verdict]
    body = f"[bold]{r.verdict.value.upper()}[/]  disputed: {r.disputed_amount}\n\n{r.summary}\n\nEvidence:\n" + \
        "\n".join(f" • {e}" for e in r.evidence)
    if result.policy_violations:
        body += "\n\n[red]" + "\n".join(result.policy_violations) + "[/]"
    console.print(Panel(body, title="Resolution", border_style=colour))
    if r.draft_email:
        console.print(Panel(r.draft_email, title="Draft email (not sent)", border_style="dim"))


def main(
    case: str = typer.Option("inv_line_math_error", help="Golden-dataset case id"),
    provider: str = typer.Option("scripted", help="scripted | local | openai | anthropic"),
    max_steps: int = typer.Option(6, min=1, max=20, help="Hard step budget"),
) -> None:
    cases = {c["id"]: c for c in load_cases()}
    if case not in cases:
        console.print(f"[red]Unknown case[/] {case!r}. Choose from: {', '.join(cases)}")
        raise typer.Exit(code=2)
    report = process_text(cases[case]["text"], DocumentType(cases[case]["doc_type"]),
                          settings=Settings(provider="heuristic"))
    if provider == "scripted":
        model = demo_script(report)
    else:
        model = build_model(Settings.from_env().model_copy(update={"provider": provider}))
    render(resolve(report, model, max_steps=max_steps), report)


if __name__ == "__main__":
    typer.run(main)
