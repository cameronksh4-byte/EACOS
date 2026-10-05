"""Test suite. Test names carry a module prefix so each curriculum checkpoint can run
its own slice, e.g. ``uv run pytest -k m3``.

  m1 - schemas & validation        m4 - pipeline, orchestration, API
  m2 - extraction & agent backends  m6 - evaluations
  m3 - PII sanitization             trainer - assessment engine & content integrity

Module 0's checkpoint lives in trainer/exercises/test_m0_basics.py and grades the
learner's own exercise file; here we only check it against the reference solutions.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from auditgate.config import Settings
from auditgate.evals.runner import load_cases, run_evals, values_match
from auditgate.extraction.extractor import (
    SYSTEM_PROMPT,
    HeuristicExtractor,
    LLMExtractor,
    build_extractor,
    detect_document_type,
    parse_amount,
    parse_date,
)
from auditgate.extraction.schemas import Bid, DocumentType, Invoice, WorkOrder
from auditgate.pipeline import Status, load_text, process_text
from auditgate.security.sanitizer import Sanitizer, luhn_valid
from trainer.assessment import allocate_days, assess, load_bank, load_curriculum

OFFLINE = Settings(provider="heuristic")


@pytest.fixture
def sanitizer() -> Sanitizer:
    return Sanitizer(spacy_model=None)


def codes(findings) -> set[str]:
    return {f.code for f in findings}


def invoice(**overrides: Any) -> Invoice:
    data: dict[str, Any] = {
        "vendor_name": "Acme", "invoice_number": "A-1", "invoice_date": "2024-01-01",
        "due_date": "2024-01-31",
        "line_items": [{"description": "Widget", "quantity": 2, "unit_price": "10.00", "amount": "20.00"}],
        "subtotal": "20.00", "tax": "1.60", "total": "21.60",
    }
    return Invoice.model_validate(data | overrides)


# --------------------------------------------------------------------------- M1 schemas


def test_m1_clean_invoice_has_no_findings():
    assert invoice().audit() == []


def test_m1_money_is_decimal_not_float():
    assert isinstance(invoice().total, Decimal)


def test_m1_decimal_arithmetic_has_no_float_drift():
    inv = invoice(line_items=[{"description": "a", "quantity": 1, "unit_price": "0.10", "amount": "0.10"},
                              {"description": "b", "quantity": 1, "unit_price": "0.20", "amount": "0.20"}],
                  subtotal="0.30", tax="0", total="0.30")
    assert "SUBTOTAL_MISMATCH" not in codes(inv.audit())


@pytest.mark.parametrize("overrides, code", [
    ({"line_items": [{"description": "W", "quantity": 3, "unit_price": "10", "amount": "20"}]}, "LINE_MATH_MISMATCH"),
    ({"subtotal": "25.00"}, "SUBTOTAL_MISMATCH"),
    ({"total": "99.00"}, "TOTAL_MISMATCH"),
    ({"due_date": "2023-12-01"}, "DUE_BEFORE_ISSUE"),
    ({"invoice_date": None}, "MISSING_DATE"),
])
def test_m1_invoice_audit_rules(overrides, code):
    assert code in codes(invoice(**overrides).audit())


def test_m1_schema_rejects_missing_required_and_bad_currency():
    with pytest.raises(ValidationError):
        Invoice.model_validate({"vendor_name": "Acme", "invoice_number": "1"})  # no total
    with pytest.raises(ValidationError):
        invoice(currency="DOLLARS")
    with pytest.raises(ValidationError):
        invoice(unexpected_field="x")  # extra='forbid' catches hallucinated keys


def test_m1_bid_and_work_order_rules():
    bid = Bid(bidder_name="B", project_name="P", total_bid="100",
              scope_items=[{"description": "x", "amount": "60"}, {"description": "y", "amount": "30"}])
    assert "BID_TOTAL_MISMATCH" in codes(bid.audit())
    wo = WorkOrder(work_order_number="1", customer_name="C", priority="  EMERGENCY ")
    assert {"NO_TASKS", "EMERGENCY_NO_CAP", "NO_SITE_ADDRESS"} <= codes(wo.audit())


# --------------------------------------------------------------------------- M2 extraction


def test_m2_parsers():
    assert parse_date("March 5, 2024") == "2024-03-05"
    assert parse_date("03/15/2024") == "2024-03-15"
    assert parse_date("not a date") is None
    assert parse_amount("$1,234.5") == "1234.50"
    assert parse_amount("(€40.00)") == "-40.00"


def test_m2_document_type_detection():
    assert detect_document_type("WORK ORDER #: 1") is DocumentType.WORK_ORDER
    assert detect_document_type("PROPOSAL\nBidder: x") is DocumentType.BID
    assert detect_document_type("Invoice #: 7") is DocumentType.INVOICE


def test_m2_default_backend_is_offline():
    assert isinstance(build_extractor(OFFLINE), HeuristicExtractor)
    assert not OFFLINE.sends_data_offsite


def test_m2_system_prompt_defends_against_injection_and_invention():
    assert "data, not instructions" in SYSTEM_PROMPT
    assert "Never calculate" in SYSTEM_PROMPT
    assert "[[PERSON_1]]" in SYSTEM_PROMPT


class _FakeCompletions:
    """Stands in for instructor's client: records the request, returns a parsed model."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        user = kwargs["messages"][-1]["content"]
        assert "[[PERSON_1]]" in user and "Maria Gonzalez" not in user  # model only sees placeholders
        return kwargs["response_model"](vendor_name="Lone Star Landscaping", invoice_number="LSL-1",
                                        bill_to="[[PERSON_1]]", total="10.00")


def test_m2_llm_extractor_round_trips_placeholders(sanitizer):
    fake = _FakeCompletions()
    extractor = LLMExtractor.__new__(LLMExtractor)
    extractor.settings, extractor.name, extractor._model = OFFLINE, "fake", "fake-model"
    extractor._client = type("C", (), {"chat": type("Ch", (), {"completions": fake})()})()

    report = process_text("Invoice #: LSL-1\nAttn: Maria Gonzalez\nTotal: $10.00",
                          DocumentType.INVOICE, settings=OFFLINE, extractor=extractor, sanitizer=sanitizer)
    assert report.data["bill_to"] == "Maria Gonzalez"  # rehydrated locally
    assert fake.calls[0]["response_model"] is Invoice
    assert fake.calls[0]["temperature"] == 0


# --------------------------------------------------------------------------- M3 sanitization


def test_m3_luhn():
    assert luhn_valid("4111 1111 1111 1111")
    assert not luhn_valid("4111 1111 1111 1112")


@pytest.mark.parametrize("value, label", [
    ("jane@example.com", "EMAIL"),
    ("(555) 123-4567", "PHONE"),
    ("555-123-4567", "PHONE"),
    ("4111-1111-1111-1111", "CREDIT_CARD"),
    ("123-45-6789", "SSN"),
    ("12-3456789", "EIN"),
    ("DE89 3704 0044 0532 0130 00", "IBAN"),
    ("10.0.0.12", "IP_ADDRESS"),
    ("742 Evergreen Terrace Dr", "ADDRESS"),
])
def test_m3_detects_identifiers(sanitizer, value, label):
    result = sanitizer.sanitize(f"Value: {value} end")
    assert value not in result.text
    assert f"[[{label}_1]]" in result.text


def test_m3_label_anchored_values_keep_their_labels(sanitizer):
    out = sanitizer.sanitize("Routing #: 021000021\nAccount No: 000123456789\nAttn: Dr. Jane Q. Doe\nTotal: $5.00")
    assert out.text.splitlines() == ["Routing #: [[ROUTING_NUMBER_1]]", "Account No: [[BANK_ACCOUNT_1]]",
                                     "Attn: [[PERSON_1]]", "Total: $5.00"]


def test_m3_does_not_redact_business_numbers(sanitizer):
    text = "Invoice #: INV-2024-0042\nDate: 2024-03-15\nLabor 3 x $1,250.00 $3,750.00\nOrder 1234567890123456"
    assert sanitizer.sanitize(text).text == text  # last number fails Luhn -> not a card


def test_m3_tokens_are_consistent_and_reversible(sanitizer):
    text = "Email a@b.co, then again a@b.co, and c@d.co"
    out = sanitizer.sanitize(text)
    assert out.text == "Email [[EMAIL_1]], then again [[EMAIL_1]], and [[EMAIL_2]]"
    assert out.vault.rehydrate(out.text) == text
    assert out.vault.rehydrate({"x": ["[[EMAIL_2]]", 5]}) == {"x": ["c@d.co", 5]}
    assert out.vault.rehydrate("[[EMAIL_9]]") == "[[EMAIL_9]]"  # unknown tokens are left alone


def test_m3_deny_terms_case_insensitive():
    out = Sanitizer(deny_terms=["Project Falcon"], spacy_model=None).sanitize("re: project FALCON budget")
    assert out.text == "re: [[REDACTED_1]] budget"


def test_m3_golden_pii_never_in_outbound_payload(sanitizer):
    for case in load_cases():
        report = process_text(case["text"], DocumentType(case["doc_type"]), settings=OFFLINE, sanitizer=sanitizer)
        for value in case["pii_values"]:
            assert value not in report.outbound_payload, (case["id"], value)


# --------------------------------------------------------------------------- M4 pipeline & API


def test_m4_pipeline_statuses(sanitizer):
    by_id = {c["id"]: c for c in load_cases()}
    run = lambda cid: process_text(by_id[cid]["text"], settings=OFFLINE, sanitizer=sanitizer)  # noqa: E731
    assert run("inv_clean").status is Status.PASS
    assert run("wo_emergency_no_cap").status is Status.REVIEW
    failed = run("inv_missing_total")
    assert failed.status is Status.FAIL and "SCHEMA_INVALID" in codes(failed.findings)


def test_m4_extractor_crash_becomes_a_finding_not_an_exception(sanitizer):
    class Boom:
        name = "boom"

        def extract(self, text, doc_type):
            raise TimeoutError("model took too long")

    report = process_text("Invoice #: 1\nTotal: $1.00", settings=OFFLINE, extractor=Boom(), sanitizer=sanitizer)
    assert report.status is Status.FAIL
    assert codes(report.findings) == {"EXTRACTION_FAILED"}


def test_m4_load_text_csv_and_excel():
    assert "Total" in load_text(b"Label,Value\nTotal,$5.00\n", "doc.csv")
    import pandas as pd

    buf = io.BytesIO()
    pd.DataFrame([["Invoice #:", "X-1"], ["Total:", "$5.00"]]).to_excel(buf, index=False, header=False)
    text = load_text(buf.getvalue(), "doc.xlsx")
    assert "Invoice #:  X-1" in text and "Total:  $5.00" in text


def test_m4_api_endpoints(monkeypatch):
    monkeypatch.setenv("AUDITGATE_PROVIDER", "heuristic")
    from auditgate import api
    from auditgate.config import get_settings

    get_settings.cache_clear()
    client = TestClient(api.app)
    assert client.get("/health").json()["status"] == "ok"
    case = next(c for c in load_cases() if c["id"] == "inv_total_inflated")
    body = client.post("/audit/text", json={"text": case["text"]}).json()
    assert body["status"] == "fail" and body["doc_type"] == "invoice"
    upload = client.post("/audit/file", files={"file": ("inv.txt", case["text"].encode(), "text/plain")}).json()
    assert upload["status"] == "fail"
    get_settings.cache_clear()


# --------------------------------------------------------------------------- M6 evaluations


def test_m6_heuristic_baseline_passes_all_gates(sanitizer):
    report = run_evals(HeuristicExtractor(), sanitizer=sanitizer, settings=OFFLINE)
    assert report.passed, report.failed_gates
    assert report.pii_values_leaked == 0 and report.pii_values_total > 0


def test_m6_eval_catches_leaks_when_sanitizer_is_disabled():
    report = run_evals(HeuristicExtractor(), sanitizer=Sanitizer(spacy_model=None, detectors=[]), settings=OFFLINE)
    assert not report.passed
    assert "pii_leakage" in report.failed_gates


def test_m6_values_match():
    assert values_match("1536.00", "1536.0")
    assert values_match("Maria Gonzalez", "maria gonzalez ")
    assert not values_match("420.00", None)


def test_m6_golden_dataset_is_well_formed():
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases) >= 10
    for c in cases:
        assert c["doc_type"] in {d.value for d in DocumentType}
        assert c["expected"]["status"] in {s.value for s in Status}


# --------------------------------------------------------------------------- trainer


def test_trainer_bank_covers_five_domains_with_valid_answers():
    bank = load_bank()
    assert len(bank) == 16
    assert len({q["domain"] for q in bank}) == 8
    for q in bank:
        assert q["answer"] in q["options"] and 1 <= q["difficulty"] <= 3


@pytest.mark.parametrize("qid", ["PY1", "PY2", "VAL1"])
def test_trainer_code_questions_answer_matches_real_output(qid):
    q = next(q for q in load_bank() if q["id"] == qid)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(q["code"], {})  # noqa: S102 - trusted, repo-authored snippet
    assert buf.getvalue().strip() == q["options"][q["answer"]]


def test_trainer_curriculum_modules_map_to_tests():
    modules = load_curriculum()["modules"]
    assert [m["id"] for m in modules] == ["M0", "M1", "M2", "M3", "M4", "M5", "M6"]
    assert "trainer/exercises/test_m0_basics.py" in modules[0]["checkpoint"]
    assert {m["build_status"] for m in modules} <= {"ready", "partial", "planned"}
    for m in modules[1:]:
        assert f"-k {m['id'].lower()}" in m["checkpoint"]
    assert sum(m["days"][1] - m["days"][0] + 1 for m in modules) == 90


def _run_exercise_checkpoint(module: str, target: str) -> subprocess.CompletedProcess:
    name = {"m0": "m0_basics", "m2": "m2_react"}[module]
    return subprocess.run(
        [sys.executable, "-m", "pytest", f"trainer/exercises/test_{name}.py", "-q", "-p", "no:cacheprovider"],
        env=os.environ | {f"{module.upper()}_TARGET": target}, capture_output=True, text=True,
    )


@pytest.mark.parametrize("module, solution", [("m0", "m0_basics"), ("m2", "m2_react")])
def test_trainer_reference_solutions_pass_checkpoint(module, solution):
    result = _run_exercise_checkpoint(module, f"trainer.exercises.solutions.{solution}")
    assert result.returncode == 0, result.stdout


def test_trainer_m2_exercise_stubs_are_unsolved():
    import trainer.exercises.m2_react as stubs

    assert _run_exercise_checkpoint("m2", "trainer.exercises.m2_react").returncode != 0
    with pytest.raises(NotImplementedError):
        stubs.run_react(lambda t: None, {}, "task", "answer")


def test_trainer_m0_exercise_stubs_are_unsolved():
    import trainer.exercises.m0_basics as stubs

    with pytest.raises(NotImplementedError):
        stubs.format_money(1)
    stubs.collect_tokens("A")
    assert stubs.collect_tokens("B") == ["A", "B"]  # exercise 10 ships with the bug on purpose


def test_trainer_allocate_days_always_fills_sprint():
    for weights in ([1, 1, 1, 1, 1], [0.2, 0.2, 0.2, 0.2, 1], [0, 0, 0, 0, 0], [3, 0.1, 7, 0.5, 2]):
        days = allocate_days(weights, 90)
        assert sum(days) == 90 and min(days) >= 3


@pytest.mark.parametrize("answers, level, start", [
    ({}, "Foundation", "M0"),
    ({q["id"]: q["answer"] for q in json.load(open("trainer/assessment_bank.json"))["questions"]}, "Architect", "M6"),
])
def test_trainer_assessment_calibrates_level(answers, level, start):
    result = assess(answers)
    assert result.level == level
    assert result.start_module == start
    assert result.plan[-1].end_day == 90


def test_trainer_weak_domain_gets_more_days_than_strong_one():
    bank = load_bank()
    answers = {q["id"]: q["answer"] for q in bank if q["domain"] != "data_security"}
    plan = {p.id: p for p in assess(answers).plan}
    assert plan["M3"].track == "full" and plan["M2"].track == "fast-track"
    assert plan["M3"].days > plan["M2"].days


def test_m3_optional_spacy_ner_is_line_scoped():
    pytest.importorskip("en_core_web_sm", reason="optional: uv pip install en_core_web_sm (see README)")
    text = "Bill To: Harbor Cafe\nPlease call Maria Gonzalez about the balance.\nTotal: $5.00"
    out = Sanitizer(spacy_model="en_core_web_sm").sanitize(text)
    assert "Maria Gonzalez" not in out.text
    assert out.text.startswith("Bill To:")  # labels are never mistaken for names
    assert "\n" not in "".join(out.vault.token_to_value.values())


def test_trainer_python_experts_skip_module_0():
    bank = load_bank()
    plan = {p.id: p for p in assess({q["id"]: q["answer"] for q in bank}).plan}
    assert plan["M0"].track == "skip" and plan["M0"].days == 0 and plan["M0"].start_day is None
    assert plan["M1"].start_day == 1 and plan["M6"].end_day == 90


def test_trainer_python_beginners_get_full_module_0():
    plan = {p.id: p for p in assess({}).plan}
    assert plan["M0"].track == "full" and plan["M0"].start_day == 1 and plan["M0"].days >= 5


def test_trainer_longer_sprint_scales_every_module():
    short = {p.id: p for p in assess({}).plan}
    long = {p.id: p for p in assess({}, sprint_days=120).plan}
    assert long["M6"].end_day == 120
    assert all(long[i].days >= short[i].days for i in short)
    with pytest.raises(ValueError):
        assess({}, sprint_days=30)


def test_m3_known_values_stay_masked_when_they_reappear(sanitizer):
    first = sanitizer.sanitize("Attn: Maria Gonzalez")
    again = sanitizer.sanitize("Thanks, Maria Gonzalez, for the update.", first.vault)
    assert again.text == "Thanks, [[PERSON_1]], for the update."
