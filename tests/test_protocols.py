"""Module 5 tests: MCP server over a real (in-memory) protocol session, OpenTelemetry traces."""

from __future__ import annotations

import json
from decimal import Decimal

import anyio
import pytest
from mcp.client.client import Client
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from auditgate import observability as obs
from auditgate.agent.__main__ import demo_script
from auditgate.agent.graph import ResolutionService
from auditgate.agent.resolver import SYSTEM_PROMPT, resolve
from auditgate.config import Settings
from auditgate.evals.runner import load_cases
from auditgate.extraction.schemas import DocumentType
from auditgate.mcp_server import build_server
from auditgate.pipeline import process_text
from auditgate.security.sanitizer import Sanitizer

CASES = {c["id"]: c for c in load_cases()}


def report_for(case_id):
    case = CASES[case_id]
    return process_text(case["text"], DocumentType(case["doc_type"]), settings=Settings(provider="heuristic"),
                        sanitizer=Sanitizer(spacy_model=None))


def mcp(fn):
    """Run fn(client) inside a real MCP client session connected to AuditGate's server."""
    async def main():
        async with Client(build_server()) as client:
            return await fn(client)
    return anyio.run(main)


# --------------------------------------------------------------------------- MCP


def test_m5_mcp_lists_read_only_tools_with_schemas():
    tools = mcp(lambda c: c.list_tools()).tools
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"audit_document", "lookup_vendor_history", "find_purchase_orders", "line_total"}
    assert by_name["audit_document"].input_schema["required"] == ["text"]
    assert all(t.annotations.read_only_hint for t in tools)


def test_m5_mcp_audit_document_never_returns_raw_pii():
    case = CASES["inv_pii_heavy"]
    result = mcp(lambda c: c.call_tool("audit_document", {"text": case["text"]}))
    text = result.content[0].text
    assert not result.is_error and json.loads(text)["status"] == "pass"
    assert [v for v in case["pii_values"] if v in text] == []
    assert json.loads(text)["data"]["bill_to"] == "[[PERSON_1]]"


def test_m5_mcp_tools_share_the_agents_validation_and_feedback():
    vendor = mcp(lambda c: c.call_tool("lookup_vendor_history", {"vendor_name": "Apex Electrical Services"}))
    assert json.loads(vendor.content[0].text)["prior_disputes"] == 1
    bad = mcp(lambda c: c.call_tool("line_total", {"quantity": "0", "unit_price": "3"}))
    assert bad.content[0].text.startswith("ERROR: Invalid arguments for line_total")
    invalid_type = mcp(lambda c: c.call_tool("audit_document", {"text": "x", "doc_type": "spaceship"}))
    assert invalid_type.is_error


def test_m5_mcp_resources():
    policy = mcp(lambda c: c.read_resource("auditgate://policy"))
    assert policy.contents[0].text == SYSTEM_PROMPT
    kinds = mcp(lambda c: c.read_resource("auditgate://document-types"))
    assert json.loads(kinds.contents[0].text) == ["invoice", "bid", "work_order"]


# --------------------------------------------------------------------------- OpenTelemetry


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, obs.tracer(provider)


def test_m5_agent_run_produces_nested_spans(spans):
    exporter, tracer = spans
    report = report_for("inv_line_math_error")
    resolve(report, demo_script(report), otel_tracer=tracer)
    finished = exporter.get_finished_spans()
    by_name = {}
    for s in finished:
        by_name.setdefault(s.name, []).append(s)
    run = by_name["agent.run"][0]
    assert len(by_name["agent.step"]) == 4
    assert all(step.parent.span_id == run.context.span_id for step in by_name["agent.step"])
    attrs = dict(run.attributes)
    assert attrs["agent.status"] == "completed" and attrs["agent.repairs"] == 1
    assert attrs["agent.prompt_version"] == obs.prompt_version(SYSTEM_PROMPT)
    errors = [s for s in finished if s.name == "tool.submit_resolution" and s.attributes["tool.is_error"]]
    assert len(errors) == 1


def test_m5_traces_contain_no_pii(spans):
    exporter, tracer = spans
    case = CASES["wo_emergency_no_cap"]
    report = report_for("wo_emergency_no_cap")
    resolve(report, demo_script(report), otel_tracer=tracer)
    dumped = json.dumps([dict(s.attributes) for s in exporter.get_finished_spans()], default=str)
    assert [v for v in case["pii_values"] if v in dumped] == []


def test_m5_human_decisions_are_traced(spans, tmp_path):
    exporter, tracer = spans
    report = report_for("inv_line_math_error")
    svc = ResolutionService(demo_script(report), tmp_path / "s.db", otel_tracer=tracer)
    svc.start(report, "t")
    svc.resume("t", {"decision": "approve", "approver": "owner"}, idempotency_key="e1")
    decision = next(s for s in exporter.get_finished_spans() if s.name == "human.decision")
    assert decision.attributes["human.decision"] == "approve" and decision.attributes["human.replayed"] is False
    trail = obs.render_trail(list(exporter.get_finished_spans()))
    assert "Human decision: approve by owner." in trail


def test_m5_cost_accounting_uses_configured_prices_only():
    prices = obs.load_prices("claude-x=3/15, local-llama=0/0")
    assert prices["claude-x"] == (Decimal("3"), Decimal("15"))
    assert obs.cost_usd("anthropic:claude-x", 1200, 350, prices) == Decimal("0.008850")
    assert obs.cost_usd("openai-compatible:unknown", 1, 1, prices) is None


def test_m5_prompt_version_and_trail_rendering(spans):
    assert obs.prompt_version("a") != obs.prompt_version("b") and len(obs.prompt_version("a")) == 12
    exporter, tracer = spans
    report = report_for("inv_line_math_error")
    resolve(report, demo_script(report), otel_tracer=tracer)
    trail = obs.render_trail(list(exporter.get_finished_spans()))
    assert trail[0].startswith("Run finished") or any(line.startswith("Run finished") for line in trail)
    assert any("returned an error" in line for line in trail)
