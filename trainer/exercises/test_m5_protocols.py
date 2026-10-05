"""Checkpoint tests for Module 5's exercise. Run with:  uv run python -m trainer.cli check M5"""

from __future__ import annotations

import hashlib
import importlib
import os
import time
from decimal import Decimal

import pytest
from pydantic import BaseModel, Field

from trainer.exercises.m5_protocols import McpTool

ex = importlib.import_module(os.environ.get("M5_TARGET", "trainer.exercises.m5_protocols"))


def test_m5_ex1_prompt_version():
    v = ex.prompt_version("You are a careful auditor.")
    assert v == hashlib.sha256(b"You are a careful auditor.").hexdigest()[:12] and len(v) == 12
    assert ex.prompt_version("You are a careful auditor!") != v


def test_m5_ex2_spans_nest_and_time():
    tracer = ex.MiniTracer()
    with tracer.span("agent.run", model="m") as run:
        with tracer.span("agent.step", index=1):
            time.sleep(0.01)
        with tracer.span("agent.step", index=2):
            pass
        run.attributes["status"] = "completed"
    names = [(s.name, s.parent) for s in tracer.finished]
    assert names == [("agent.step", "agent.run"), ("agent.step", "agent.run"), ("agent.run", None)]
    assert tracer.finished[0].duration_ms >= 10
    assert tracer.finished[-1].attributes == {"model": "m", "status": "completed"}


def test_m5_ex2_span_closes_even_when_the_block_fails():
    tracer = ex.MiniTracer()
    with pytest.raises(ValueError):
        with tracer.span("tool.lookup"):
            raise ValueError("boom")
    assert [s.name for s in tracer.finished] == ["tool.lookup"]
    with tracer.span("next"):
        pass
    assert tracer.finished[-1].parent is None  # the failed span didn't stay "open"


def test_m5_ex3_safe_attributes():
    attrs = {"customer": "Maria Gonzalez", "tool": "lookup", "excerpt": "x" * 80, "tokens": 120}
    safe = ex.safe_attributes(attrs, {"customer"})
    assert safe == {"customer": "[REDACTED]", "tool": "lookup", "excerpt": "x" * 50 + "…", "tokens": 120}
    assert attrs["customer"] == "Maria Gonzalez"  # original untouched


def test_m5_ex4_cost():
    assert ex.cost_usd(1_000_000, 0, "3", "15") == Decimal("3.000000")
    assert ex.cost_usd(1200, 350, "3", "15") == Decimal("0.008850")
    assert isinstance(ex.cost_usd(1, 1, "0.1", "0.2"), Decimal)


class LineTotalIn(BaseModel):
    quantity: int = Field(gt=0)
    unit_price: float


TOOLS = [McpTool("line_total", "quantity x unit price", LineTotalIn, lambda i: f"{i.quantity * i.unit_price:.2f}")]


def test_m5_ex5_tools_list():
    res = ex.handle_mcp_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, TOOLS)
    assert res["jsonrpc"] == "2.0" and res["id"] == 1
    tool = res["result"]["tools"][0]
    assert tool["name"] == "line_total" and set(tool["inputSchema"]["properties"]) == {"quantity", "unit_price"}


def test_m5_ex5_tools_call_success_and_tool_error():
    ok = ex.handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "line_total", "arguments": {"quantity": 3, "unit_price": 2.5}}},
                               TOOLS)
    assert ok["result"] == {"content": [{"type": "text", "text": "7.50"}], "isError": False}
    bad = ex.handle_mcp_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                 "params": {"name": "line_total", "arguments": {"quantity": 0, "unit_price": 1}}},
                                TOOLS)
    assert bad["result"]["isError"] is True and "quantity" in bad["result"]["content"][0]["text"]


def test_m5_ex5_protocol_errors():
    unknown = ex.handle_mcp_request({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                     "params": {"name": "rm_rf", "arguments": {}}}, TOOLS)
    assert unknown["error"]["code"] == -32602 and "rm_rf" in unknown["error"]["message"]
    nope = ex.handle_mcp_request({"jsonrpc": "2.0", "id": 5, "method": "resources/delete"}, TOOLS)
    assert nope == {"jsonrpc": "2.0", "id": 5, "error": {"code": -32601, "message": "Method not found"}}
