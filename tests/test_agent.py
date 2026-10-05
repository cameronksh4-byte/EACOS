"""Module 2 tests for the production agent (auditgate/agent). Run with: uv run pytest -k m2"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from auditgate.agent.__main__ import demo_script
from auditgate.agent.loop import run_agent
from auditgate.agent.models import (
    INVALID_JSON_KEY,
    AnthropicModel,
    ModelTurn,
    OpenAICompatibleModel,
    ScriptedModel,
    ToolCall,
    ToolResult,
)
from auditgate.agent.resolver import (
    Resolution,
    Verdict,
    VendorStore,
    build_model,
    build_registry,
    enforce_policy,
    resolve,
)
from auditgate.agent.tools import Tool, ToolRegistry
from auditgate.config import Settings
from auditgate.evals.runner import load_cases
from auditgate.extraction.schemas import DocumentType
from auditgate.pipeline import AuditReport, process_text
from auditgate.security.sanitizer import Sanitizer

CASES = {c["id"]: c for c in load_cases()}


def report_for(case_id: str) -> AuditReport:
    case = CASES[case_id]
    return process_text(case["text"], DocumentType(case["doc_type"]), settings=Settings(provider="heuristic"),
                        sanitizer=Sanitizer(spacy_model=None))


def submit(call_id: str, **fields) -> ModelTurn:
    return ModelTurn(tool_calls=[ToolCall(call_id, "submit_resolution", fields)])


# --------------------------------------------------------------------------- tools


class Echo(BaseModel):
    text: str
    times: int = 1


def _boom(_: Echo) -> None:
    raise KeyError("vendor_id")


REGISTRY = ToolRegistry([Tool("echo", "Repeat text", Echo, lambda e: {"out": e.text * e.times}),
                         Tool("boom", "Always fails", Echo, _boom)])


def test_m2_tool_schema_comes_from_pydantic():
    spec = REGISTRY.specs()[0]
    assert spec.input_schema["properties"].keys() == {"text", "times"}
    assert spec.input_schema["required"] == ["text"]
    assert "title" not in spec.input_schema


def test_m2_registry_rejects_duplicate_names():
    with pytest.raises(ValueError):
        ToolRegistry([Tool("echo", "a", Echo, print), Tool("echo", "b", Echo, print)])


@pytest.mark.parametrize("call, expected", [
    (ToolCall("1", "nope", {}), ["Unknown tool 'nope'", "echo, boom"]),
    (ToolCall("2", "echo", {INVALID_JSON_KEY: "{text: hi"}), ["not valid JSON"]),
    (ToolCall("3", "echo", {"times": "many"}), ["- text: Field required", "- times:", 'you sent "many"',
                                                "call echo again"]),
    (ToolCall("4", "boom", {"text": "x"}), ["boom failed: KeyError: 'vendor_id'", "test_agent.py:"]),
])
def test_m2_errors_are_returned_with_actionable_detail(call, expected):
    result, parsed = REGISTRY.execute(call)
    assert result.is_error and parsed is None and result.call_id == call.id
    for fragment in expected:
        assert fragment in result.content


def test_m2_successful_call_returns_json_and_validated_input():
    result, parsed = REGISTRY.execute(ToolCall("5", "echo", {"text": "ab", "times": 2}))
    assert not result.is_error and json.loads(result.content) == {"out": "abab"}
    assert isinstance(parsed, Echo)


# --------------------------------------------------------------------------- loop


def test_m2_loop_pii_boundary():
    """The model only sees placeholders; tools get real values; tool output is re-sanitized."""
    received = []

    def lookup(q: Echo) -> str:
        received.append(q.text)
        return f"{q.text} can be reached at maria@example.com"

    registry = ToolRegistry([Tool("lookup", "Look up a contact", Echo, lookup),
                             Tool("done", "Finish", Echo, lambda e: "ok")])

    def first(transcript):
        assert "Maria Gonzalez" not in transcript[0] and "[[PERSON_1]]" in transcript[0]
        return ModelTurn(tool_calls=[ToolCall("a", "lookup", {"text": "[[PERSON_1]]"})])

    def second(transcript):
        seen = transcript[-1][0].content
        assert "maria@example.com" not in seen and "Maria Gonzalez" not in seen
        return ModelTurn(tool_calls=[ToolCall("b", "done", {"text": "Emailed [[PERSON_1]]"})])

    run = run_agent(ScriptedModel([first, second]), registry, system="", task="Attn: Maria Gonzalez",
                    final_tool="done", sanitizer=Sanitizer(spacy_model=None))
    assert received == ["Maria Gonzalez"]
    assert run.status == "completed" and run.final.text == "Emailed Maria Gonzalez"  # rehydrated locally
    assert "Maria Gonzalez" not in json.dumps([s.tool_calls[0].arguments for s in run.steps])  # trace is PII-free


def test_m2_loop_budget_and_nudge():
    model = ScriptedModel([ModelTurn(text="Let me think...")] * 5)
    run = run_agent(model, REGISTRY, system="", task="t", final_tool="echo", max_steps=3)
    assert run.status == "budget_exhausted" and run.final is None and len(run.steps) == 3
    assert len(model.seen) == 3
    assert "did not call a tool" in model.seen[1][-1]


def test_m2_loop_rejects_unregistered_final_tool():
    with pytest.raises(ValueError):
        run_agent(ScriptedModel([]), REGISTRY, system="", task="t", final_tool="missing")


# --------------------------------------------------------------------------- resolver


def test_m2_demo_resolves_line_math_error_with_one_self_correction():
    report = report_for("inv_line_math_error")
    result = resolve(report, demo_script(report))
    assert result.status == "resolved" and not result.policy_violations
    assert result.resolution.verdict is Verdict.DISPUTE
    assert result.resolution.disputed_amount == Decimal("90.00")
    assert "Apex Electrical Services" in result.resolution.draft_email
    assert result.run.repairs == 1 and len(result.run.steps) == 4


def test_m2_policy_overrides_model_approving_a_failed_invoice():
    report = report_for("inv_total_inflated")
    model = ScriptedModel([submit("a", verdict="approve", summary="Looks fine to me, approve it.",
                                  evidence=["the model said so"])])
    result = resolve(report, model)
    assert result.status == "escalated" and result.resolution.verdict is Verdict.ESCALATE
    assert any("cannot be auto-approved" in v for v in result.policy_violations)


def test_m2_policy_caps_disputed_amount_at_document_total():
    report = report_for("inv_total_inflated")
    res = Resolution(verdict="dispute", summary="Overbilled by a huge amount here.", disputed_amount="5000.00",
                     evidence=["x"], draft_email="Hello")
    fixed, violations = enforce_policy(res, report)
    assert fixed.verdict is Verdict.ESCALATE and "exceeds document total" in violations[0]


def test_m2_resolution_must_be_internally_consistent():
    with pytest.raises(ValidationError, match="draft_email"):
        Resolution(verdict="dispute", summary="Vendor overbilled us on line 1.", disputed_amount="10", evidence=["x"])
    with pytest.raises(ValidationError, match="approval cannot"):
        Resolution(verdict="approve", summary="Fine, but also dispute 5?", disputed_amount="5", evidence=["x"])


def test_m2_agent_that_never_finishes_is_escalated():
    report = report_for("inv_line_math_error")
    model = ScriptedModel([ModelTurn(tool_calls=[ToolCall(str(i), "lookup_vendor_history",
                                                          {"vendor_name": "Apex Electrical Services"})])
                           for i in range(10)])
    result = resolve(report, model, max_steps=3)
    assert result.status == "escalated" and result.resolution is None
    assert "3 steps" in result.policy_violations[0]


def test_m2_vendor_store_and_tools():
    store = VendorStore()
    assert store.get("apex electrical  services.")["prior_disputes"] == 1
    registry = build_registry(store)
    out, _ = registry.execute(ToolCall("1", "line_total", {"quantity": "10", "unit_price": "45.00"}))
    assert json.loads(out.content) == {"line_total": "450.00"}
    missing, _ = registry.execute(ToolCall("2", "lookup_vendor_history", {"vendor_name": "Nobody Inc"}))
    assert json.loads(missing.content)["found"] is False


def test_m2_build_model_requires_a_real_provider():
    with pytest.raises(ValueError, match="scripted"):
        build_model(Settings(provider="heuristic"))


# --------------------------------------------------------------------------- SDK adapters (fake clients)


def test_m2_anthropic_adapter_formats_and_parses():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            content=[SimpleNamespace(type="text", text="Checking."),
                     SimpleNamespace(type="tool_use", id="tu_2", name="echo", input={"text": "hi"})])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    transcript = ["task", ModelTurn(text="", tool_calls=[ToolCall("tu_1", "echo", {"text": "a"})]),
                  [ToolResult("tu_1", "echo", "boom", is_error=True)], "please call a tool"]
    turn = AnthropicModel("test-model", client=client).complete("sys", transcript, REGISTRY.specs())

    msgs = captured["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]  # consecutive user turns merged
    assert msgs[1]["content"][0] == {"type": "tool_use", "id": "tu_1", "name": "echo", "input": {"text": "a"}}
    assert msgs[2]["content"][0]["type"] == "tool_result" and msgs[2]["content"][0]["is_error"] is True
    assert msgs[2]["content"][1] == {"type": "text", "text": "please call a tool"}
    assert captured["system"] == "sys" and captured["tools"][0]["input_schema"]["required"] == ["text"]
    assert turn.text == "Checking." and turn.tool_calls[0].arguments == {"text": "hi"}
    assert (turn.input_tokens, turn.output_tokens) == (11, 7)


def test_m2_openai_adapter_formats_and_survives_bad_json():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        call = SimpleNamespace(id="c9", function=SimpleNamespace(name="echo", arguments="{text: oops"))
        message = SimpleNamespace(content=None, tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                               usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    transcript = ["task", ModelTurn(tool_calls=[ToolCall("c1", "echo", {"text": "a"})]),
                  [ToolResult("c1", "echo", "ok")]]
    turn = OpenAICompatibleModel("m", client=client).complete("sys", transcript, REGISTRY.specs())

    msgs = captured["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool"]
    assert json.loads(msgs[2]["tool_calls"][0]["function"]["arguments"]) == {"text": "a"}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}
    assert turn.tool_calls[0].arguments == {INVALID_JSON_KEY: "{text: oops"}
    result, _ = REGISTRY.execute(turn.tool_calls[0])
    assert result.is_error and "not valid JSON" in result.content
