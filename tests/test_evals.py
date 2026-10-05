"""Module 6 tests: agent evals (Pass@k, tool selection, safety), synthetic data, clustering, CI."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
import yaml

from auditgate.agent.models import ModelTurn, ScriptedModel, ToolCall
from auditgate.config import Settings
from auditgate.evals import synth
from auditgate.evals.agent_runner import compare, load_agent_cases, replay_model, run_agent_evals
from auditgate.evals.cluster import Failure, cluster_failures, failures_from_report, kmeans, tfidf
from auditgate.evals.metrics import exact_match, pass_at_k, precision_recall
from auditgate.evals.runner import DATASET, load_cases, run_evals
from auditgate.extraction.extractor import HeuristicExtractor
from auditgate.security.sanitizer import Sanitizer

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- metrics


def test_m6_metrics():
    assert pass_at_k(5, 2, 1) == pytest.approx(0.4) and pass_at_k(4, 2, 3) == 1.0
    assert precision_recall({"a", "x"}, {"a", "b"}, {"c"}) == (0.5, 0.5)
    assert exact_match("90", "90.00") and exact_match("Robert  Chen", "robert chen") and not exact_match(None, "0")


# --------------------------------------------------------------------------- agent evals


def test_m6_replay_agent_eval_is_perfect():
    report = run_agent_evals(replay_model)
    assert report.passed, report.failed_gates
    assert report.metrics["pass_at_1"] == 1.0 and report.metrics["schema_compliance"] == 1.0
    assert report.metrics["tool_recall"] == 1.0 and report.metrics["forbidden_verdicts"] == 0
    assert len(report.cases) == len(load_agent_cases()) >= 8


def approve_everything(case):
    return ScriptedModel([ModelTurn(tool_calls=[ToolCall("1", "submit_resolution", {
        "verdict": "approve", "summary": "Everything looks fine to me here.", "evidence": ["vibes"]})])])


def test_m6_agent_eval_catches_a_reckless_model():
    report = run_agent_evals(approve_everything, provider="reckless")
    assert not report.passed
    assert "pass_at_1" in report.failed_gates and "tool_recall" in report.failed_gates
    # Policy converts approvals of FAILED documents to escalations; approvals that slip through are counted.
    assert report.metrics["pass_at_1"] < 0.75


def test_m6_pass_at_k_rewards_a_model_that_succeeds_sometimes():
    flip = itertools.cycle([True, False, False])
    cases = [c for c in load_agent_cases() if c["id"] == "agent_line_math"]

    def flaky(case):
        return replay_model(case) if next(flip) else approve_everything(case)

    report = run_agent_evals(flaky, provider="flaky", n=3, k=3, cases=cases)
    score = report.cases[0]
    assert score.pass_at_1 == pytest.approx(1 / 3) and score.pass_at_k == 1.0


def test_m6_schema_compliance_counts_invalid_submissions():
    def sloppy(case):
        turns = [ModelTurn(tool_calls=[ToolCall("x", "submit_resolution", {"verdict": "maybe"})])]
        return ScriptedModel(turns + replay_model(case).turns)

    cases = [c for c in load_agent_cases() if c["id"] == "agent_no_tasks"]
    report = run_agent_evals(sloppy, provider="sloppy", cases=cases)
    assert report.metrics["schema_compliance"] == 0.5 and report.cases[0].pass_at_1 == 1.0


def test_m6_before_after_comparison():
    before = {"pass_at_1": 0.9, "tool_recall": 0.9, "forbidden_verdicts": 0.0, "avg_steps": 3.0}
    after = {"pass_at_1": 0.8, "tool_recall": 0.92, "forbidden_verdicts": 0.0, "avg_steps": 5.0}
    rows, regressions = compare(before, after)
    assert regressions == ["pass_at_1"] and len(rows) == 4
    assert compare(before, before | {"forbidden_verdicts": 1.0})[1] == ["forbidden_verdicts"]


# --------------------------------------------------------------------------- synthetic data


def test_m6_synthetic_generation_is_deterministic_and_committed():
    generated = synth.generate()
    assert generated == synth.generate()
    committed = json.loads((DATASET.with_name("synthetic_dataset.json")).read_text(encoding="utf-8"))["cases"]
    assert committed == generated, "regenerate with: uv run python -m auditgate.evals.synth"
    kinds = {c["id"].split("__")[1].rstrip("0123456789") for c in generated}
    assert {"inflate_line", "inject", "long_dates", "spaced_phone", "spelled_email", "noisy_layout"} <= kinds
    assert len(load_cases(DATASET)) + len(generated) >= 40


def test_m6_full_dataset_passes_every_gate():
    cases = load_cases(DATASET) + synth.generate()
    report = run_evals(HeuristicExtractor(), sanitizer=Sanitizer(spacy_model=None),
                       settings=Settings(provider="heuristic"), cases=cases)
    assert report.passed, report.failed_gates
    assert report.pii_values_leaked == 0 and report.pii_values_total > 60


def test_m6_mutations_derive_correct_expectations():
    clean = next(c for c in load_cases(DATASET) if c["id"] == "inv_clean")
    inflated = synth.inflate_line(clean)
    assert inflated["expected"]["status"] == "fail" and "1,460.00" in inflated["text"]
    assert synth.inflate_line(next(c for c in load_cases(DATASET) if c["id"] == "inv_total_inflated")) is None
    spelled = synth.spelled_email(next(c for c in load_cases(DATASET) if c["id"] == "inv_pii_heavy"))
    assert "maria.gonzalez at lonestarland dot example" in spelled["pii_values"]


def test_m6_model_proposed_cases_are_validated_candidates():
    proposal = {"cases": [{"doc_type": "invoice", "text": "Vendor: Fictional Co\nInvoice #: F-1\nTotal: $1,00O.00 (letter O)",
                           "why_adversarial": "letter O instead of zero in the total",
                           "expected_status": "fail", "expected_finding_codes": ["SCHEMA_INVALID"]}]}
    bad_then_good = ScriptedModel([
        ModelTurn(tool_calls=[ToolCall("1", "propose_cases", {"cases": [{"doc_type": "receipt"}]})]),
        ModelTurn(tool_calls=[ToolCall("2", "propose_cases", proposal)]),
    ])
    out = synth.propose_with_model(bad_then_good, n=1)
    assert len(out) == 1 and out[0].expected_status == "fail"


# --------------------------------------------------------------------------- clustering


def test_m6_clustering_groups_failures_by_root_cause():
    failures = [Failure("a", "leaked pii maria at x dot com"), Failure("b", "leaked pii bob at y dot org"),
                Failure("c", "scope_items.1.amount: expected 21500.00, got None"),
                Failure("d", "scope_items.2.amount: expected 3200.00, got None"),
                Failure("e", "verdict approve, expected one of escalate"), Failure("f", "forbidden verdict approve")]
    clusters = cluster_failures(failures, k=3)
    groups = sorted(sorted(m.case_id for m in c.members) for c in clusters)
    assert groups == [["a", "b"], ["c", "d"], ["e", "f"]]
    assert kmeans(tfidf(["x"]), 5) == [0] and cluster_failures([], 3) == []


def test_m6_failures_are_read_from_both_report_types():
    doc = {"cases": [{"id": "d1", "field_errors": ["total: expected 1, got 2"], "missing_findings": ["X"],
                      "leaked": ["secret"], "status_expected": "fail", "status_actual": "pass"}]}
    agent = {"cases": [{"id": "a1", "attempts": [{"failure": "forbidden verdict approve"}, {"failure": None}]}]}
    texts = [f.text for f in failures_from_report(doc) + failures_from_report(agent)]
    assert texts == ["total: expected 1, got 2", "missing finding X", "leaked pii secret",
                     "status pass instead of fail", "forbidden verdict approve"]


# --------------------------------------------------------------------------- CI


def test_m6_ci_workflow_runs_tests_and_both_eval_gates():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    commands = " ".join(step.get("run", "") for step in workflow["jobs"]["test"]["steps"])
    for needle in ("uv sync --frozen", "pytest", "auditgate.evals.runner --dataset all", "auditgate.evals.agent_runner"):
        assert needle in commands
