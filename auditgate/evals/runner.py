"""Automated audit of the audit engine.

Runs every case in ``golden_dataset.json`` through the full pipeline and scores:

* field_accuracy    - extracted values match the hand-labelled truth
* status_accuracy   - pass / review / fail verdict matches
* finding_recall    - every expected audit finding was raised
* schema_adherence  - valid docs validate; broken docs are rejected (never guessed)
* pii_leakage       - sensitive values found in the outbound payload (must be 0)

Exit code is non-zero when any gate fails, so this doubles as a CI check:

    uv run python -m auditgate.evals.runner                 # offline heuristic baseline
    uv run python -m auditgate.evals.runner --provider local
    uv run python -m auditgate.evals.runner --provider anthropic --report eval_report.json
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

from auditgate.config import Settings
from auditgate.extraction.extractor import Extractor, build_extractor
from auditgate.extraction.schemas import DocumentType
from auditgate.pipeline import process_text
from auditgate.security.sanitizer import Sanitizer

DATASET = Path(__file__).with_name("golden_dataset.json")

GATES = {
    "field_accuracy": 0.95,
    "status_accuracy": 0.90,
    "finding_recall": 0.90,
    "schema_adherence": 1.00,
}


class CaseResult(BaseModel):
    id: str
    status_expected: str
    status_actual: str
    fields_total: int
    fields_correct: int
    field_errors: list[str] = Field(default_factory=list)
    findings_expected: int
    findings_hit: int
    missing_findings: list[str] = Field(default_factory=list)
    schema_ok: bool
    leaked: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    extractor: str
    cases: list[CaseResult]
    metrics: dict[str, float]
    pii_values_total: int
    pii_values_leaked: int
    passed: bool
    failed_gates: list[str]


def _get(data: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(data, list):
            idx = int(part)
            data = data[idx] if idx < len(data) else None
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


def values_match(expected: Any, actual: Any) -> bool:
    if actual is None:
        return expected is None
    try:
        return Decimal(str(expected)) == Decimal(str(actual))
    except InvalidOperation:
        return str(expected).strip().lower() == str(actual).strip().lower()


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def run_evals(
    extractor: Extractor,
    sanitizer: Sanitizer | None = None,
    settings: Settings | None = None,
    dataset: Path = DATASET,
) -> EvalReport:
    settings = settings or Settings()
    sanitizer = sanitizer or Sanitizer(spacy_model=None)
    results: list[CaseResult] = []
    pii_total = pii_leaked = 0

    for case in load_cases(dataset):
        exp = case["expected"]
        report = process_text(case["text"], DocumentType(case["doc_type"]),
                              settings=settings, extractor=extractor, sanitizer=sanitizer)
        codes = {f.code for f in report.findings}

        field_errors = []
        for path, want in exp["fields"].items():
            got = _get(report.data, path) if report.data else None
            if not values_match(want, got):
                field_errors.append(f"{path}: expected {want!r}, got {got!r}")

        expects_reject = "SCHEMA_INVALID" in exp["finding_codes"]
        rejected = bool(codes & {"SCHEMA_INVALID", "EXTRACTION_FAILED"})
        leaked = [v for v in case["pii_values"] if v in report.outbound_payload]
        pii_total += len(case["pii_values"])
        pii_leaked += len(leaked)

        missing = [c for c in exp["finding_codes"] if c not in codes]
        results.append(CaseResult(
            id=case["id"], status_expected=exp["status"], status_actual=report.status.value,
            fields_total=len(exp["fields"]), fields_correct=len(exp["fields"]) - len(field_errors),
            field_errors=field_errors, findings_expected=len(exp["finding_codes"]),
            findings_hit=len(exp["finding_codes"]) - len(missing), missing_findings=missing,
            schema_ok=(rejected == expects_reject), leaked=leaked,
        ))

    def ratio(num: int, den: int) -> float:
        return round(num / den, 4) if den else 1.0

    metrics = {
        "field_accuracy": ratio(sum(r.fields_correct for r in results), sum(r.fields_total for r in results)),
        "status_accuracy": ratio(sum(r.status_expected == r.status_actual for r in results), len(results)),
        "finding_recall": ratio(sum(r.findings_hit for r in results), sum(r.findings_expected for r in results)),
        "schema_adherence": ratio(sum(r.schema_ok for r in results), len(results)),
        "pii_leak_rate": ratio(pii_leaked, pii_total) if pii_total else 0.0,
    }
    failed = [name for name, floor in GATES.items() if metrics[name] < floor]
    if pii_leaked:
        failed.append("pii_leakage")
    return EvalReport(extractor=extractor.name, cases=results, metrics=metrics,
                      pii_values_total=pii_total, pii_values_leaked=pii_leaked,
                      passed=not failed, failed_gates=failed)


def render(report: EvalReport, console: Console) -> None:
    table = Table(title=f"AuditGate eval - extractor: {report.extractor}", show_lines=False)
    for col in ("case", "status (exp/got)", "fields", "findings", "schema", "PII leaks"):
        table.add_column(col)
    for r in report.cases:
        ok = r.status_expected == r.status_actual
        table.add_row(
            r.id,
            f"[{'green' if ok else 'red'}]{r.status_expected}/{r.status_actual}[/]",
            f"{r.fields_correct}/{r.fields_total}",
            f"{r.findings_hit}/{r.findings_expected}",
            "[green]ok[/]" if r.schema_ok else "[red]wrong[/]",
            "[green]0[/]" if not r.leaked else f"[red]{len(r.leaked)}[/]",
        )
    console.print(table)
    for r in report.cases:
        for err in r.field_errors + [f"missing finding {c}" for c in r.missing_findings]:
            console.print(f"  [yellow]{r.id}[/]: {err}")
        for v in r.leaked:
            console.print(f"  [bold red]{r.id}: LEAKED {v!r}[/]")

    metrics = Table(title="Gates")
    metrics.add_column("metric")
    metrics.add_column("score")
    metrics.add_column("gate")
    for name, value in report.metrics.items():
        gate = "== 0" if name == "pii_leak_rate" else f">= {GATES[name]:.2f}"
        good = value == 0 if name == "pii_leak_rate" else value >= GATES[name]
        metrics.add_row(name, f"[{'green' if good else 'red'}]{value:.2%}[/]", gate)
    console.print(metrics)
    verdict = "[bold green]PASSED[/]" if report.passed else f"[bold red]FAILED[/] ({', '.join(report.failed_gates)})"
    console.print(f"Result: {verdict}  |  PII values checked: {report.pii_values_total}, leaked: {report.pii_values_leaked}")


def main(
    provider: str = typer.Option(None, help="heuristic | local | openai | anthropic (default: from .env)"),
    report_path: Path = typer.Option(None, "--report", help="Write the full JSON report here"),
    spacy: bool = typer.Option(False, help="Also use the spaCy NER model configured in .env"),
) -> None:
    settings = Settings.from_env()
    if provider:
        settings = settings.model_copy(update={"provider": provider})
    sanitizer = Sanitizer(deny_terms=settings.deny_terms, redact_orgs=settings.redact_orgs,
                          spacy_model=settings.spacy_model if spacy else None)
    report = run_evals(build_extractor(settings), sanitizer=sanitizer, settings=settings)
    render(report, Console())
    if report_path:
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    raise typer.Exit(code=0 if report.passed else 1)


if __name__ == "__main__":
    typer.run(main)
