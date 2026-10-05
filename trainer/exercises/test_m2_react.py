"""Checkpoint tests for Module 2's exercise. Run with:  uv run python -m trainer.cli check M2

These grade YOUR file (trainer/exercises/m2_react.py). The main test suite runs the
same tests against the reference solutions by setting M2_TARGET.
"""

from __future__ import annotations

import importlib
import json
import os
from decimal import Decimal

import pytest
from pydantic import BaseModel, Field, ValidationError

from trainer.exercises.m2_react import ModelTurn, Tool, ToolCall

ex = importlib.import_module(os.environ.get("M2_TARGET", "trainer.exercises.m2_react"))


class LineTotalIn(BaseModel):
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal


class Answer(BaseModel):
    total: Decimal
    reason: str = Field(min_length=5)


def broken(_: BaseModel) -> None:
    raise KeyError("vendor_id")


TOOLS = {
    "line_total": Tool("line_total", "quantity x unit price", LineTotalIn,
                       lambda i: {"line_total": str(i.quantity * i.unit_price)}),
    "lookup": Tool("lookup", "always fails", LineTotalIn, broken),
    "answer": Tool("answer", "submit the final answer", Answer, lambda a: "ok"),
}


def test_m2_ex1_tool_schema():
    schema = ex.tool_schema(TOOLS["line_total"])
    assert schema["name"] == "line_total" and schema["description"] == "quantity x unit price"
    assert set(schema["input_schema"]["properties"]) == {"quantity", "unit_price"}
    assert "quantity" in schema["input_schema"]["required"]


def test_m2_ex2_format_validation_error_names_every_field():
    with pytest.raises(ValidationError) as info:
        Answer.model_validate({"total": "lots", "reason": "x"})
    message = ex.format_validation_error(info.value, "answer")
    assert "answer" in message
    assert "total" in message and "reason" in message
    assert "decimal" in message.lower()  # Pydantic's own explanation is included


def test_m2_ex3_execute_tool_success():
    result, parsed = ex.execute_tool(ToolCall("1", "line_total", {"quantity": 3, "unit_price": "2.50"}), TOOLS)
    assert not result.is_error and result.call_id == "1"
    assert json.loads(result.content) == {"line_total": "7.50"}
    assert parsed.quantity == 3


def test_m2_ex3_execute_tool_never_raises():
    unknown, p1 = ex.execute_tool(ToolCall("2", "delete_everything", {}), TOOLS)
    assert unknown.is_error and p1 is None and "line_total" in unknown.content
    invalid, p2 = ex.execute_tool(ToolCall("3", "line_total", {"quantity": 0, "unit_price": 1}), TOOLS)
    assert invalid.is_error and p2 is None and "quantity" in invalid.content
    crash, p3 = ex.execute_tool(ToolCall("4", "lookup", {"quantity": 1, "unit_price": 1}), TOOLS)
    assert crash.is_error and p3 is None and "KeyError" in crash.content and "vendor_id" in crash.content


def scripted(*turns: ModelTurn):
    seen = []

    def model(transcript):
        seen.append(list(transcript))
        return turns[len(seen) - 1] if len(seen) <= len(turns) else ModelTurn(text="...")

    model.seen = seen
    return model


def test_m2_ex4_loop_uses_tools_then_answers():
    model = scripted(
        ModelTurn(tool_calls=[ToolCall("a", "line_total", {"quantity": 2, "unit_price": "10"})]),
        ModelTurn(tool_calls=[ToolCall("b", "answer", {"total": "20", "reason": "2 x 10"})]),
    )
    result = ex.run_react(model, TOOLS, "What is 2 x 10?", final_tool="answer")
    assert result.stopped == "answered" and result.steps == 2
    assert result.answer.total == Decimal("20")
    second_call = model.seen[1]
    assert second_call[0] == "What is 2 x 10?"
    assert '"line_total": "20"' in second_call[-1][0].content  # the model saw the tool result


def test_m2_ex4_loop_feeds_errors_back_for_self_correction():
    model = scripted(
        ModelTurn(tool_calls=[ToolCall("a", "answer", {"total": "twenty", "reason": "trust me"})]),
        ModelTurn(tool_calls=[ToolCall("b", "answer", {"total": "20", "reason": "fixed it"})]),
    )
    result = ex.run_react(model, TOOLS, "task", final_tool="answer")
    assert result.stopped == "answered" and result.steps == 2
    feedback = model.seen[1][-1][0]
    assert feedback.is_error and "total" in feedback.content


def test_m2_ex4_loop_stops_at_budget_and_nudges_chatty_models():
    model = scripted(ModelTurn(text="I think the answer is 20."), ModelTurn(text="Still thinking."),
                     ModelTurn(text="Hmm."), ModelTurn(text="never reached"))
    result = ex.run_react(model, TOOLS, "task", final_tool="answer", max_steps=3)
    assert result.stopped == "budget" and result.answer is None and result.steps == 3
    assert len(model.seen) == 3                       # hard stop: the 4th turn never runs
    assert isinstance(model.seen[1][-1], str)         # a reminder was added after the chatty turn
