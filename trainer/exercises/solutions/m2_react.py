"""Reference solutions for trainer/exercises/m2_react.py. Compare with auditgate/agent/ too."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from trainer.exercises.m2_react import ModelFn, ReactResult, Tool, ToolCall, ToolResult


def tool_schema(tool: Tool) -> dict[str, Any]:
    return {"name": tool.name, "description": tool.description,
            "input_schema": tool.input_model.model_json_schema()}


def format_validation_error(exc: ValidationError, tool_name: str) -> str:
    lines = [f"Invalid arguments for {tool_name}:"]
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "(arguments)"
        lines.append(f"- {field}: {err['msg']}")
    lines.append(f"Fix these fields and call {tool_name} again.")
    return "\n".join(lines)


def execute_tool(call: ToolCall, tools: dict[str, Tool]) -> tuple[ToolResult, BaseModel | None]:
    tool = tools.get(call.name)
    if tool is None:
        return ToolResult(call.id, f"Unknown tool {call.name!r}. Available: {', '.join(tools)}", True), None
    try:
        parsed = tool.input_model.model_validate(call.arguments)
    except ValidationError as exc:
        return ToolResult(call.id, format_validation_error(exc, call.name), True), None
    try:
        output = tool.fn(parsed)
    except Exception as exc:
        return ToolResult(call.id, f"{call.name} failed: {type(exc).__name__}: {exc}", True), None
    content = json.dumps(output, default=str) if isinstance(output, (dict, list)) else str(output)
    return ToolResult(call.id, content), parsed


def run_react(model: ModelFn, tools: dict[str, Tool], task: str, final_tool: str,
              max_steps: int = 3) -> ReactResult:
    transcript: list[Any] = [task]
    for step in range(1, max_steps + 1):
        turn = model(transcript)
        transcript.append(turn)
        results = []
        for call in turn.tool_calls:
            result, parsed = execute_tool(call, tools)
            if call.name == final_tool and parsed is not None:
                return ReactResult(answer=parsed, steps=step, stopped="answered")
            results.append(result)
        transcript.append(results if results else f"Call a tool. When you are done, call {final_tool}.")
    return ReactResult(answer=None, steps=max_steps, stopped="budget")
