"""Tools: typed definitions, schema generation, and execution with feedback injection.

Two rules make tool calling reliable:

1. **The schema comes from a Pydantic model.** The model is shown exactly the JSON
   Schema we validate against, so "what we asked for" and "what we accept" can't drift.
2. **Errors are returned, not raised.** A bad call becomes a short, precise tool
   result marked ``is_error`` - which field, what was wrong, what to do - so the model
   can fix its own call on the next step instead of the run crashing.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from auditgate.agent.models import INVALID_JSON_KEY, ToolCall, ToolResult, ToolSpec


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Any], Any]

    def spec(self) -> ToolSpec:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(name=self.name, description=self.description, input_schema=schema)


def format_validation_error(exc: ValidationError, tool_name: str) -> str:
    """Turn a Pydantic ValidationError into instructions a model can act on."""
    lines = [f"Invalid arguments for {tool_name}:"]
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "(arguments)"
        got = err.get("input")
        got_text = f" (you sent {json.dumps(got, default=str)[:80]})" if err["type"] != "missing" else ""
        lines.append(f"- {field}: {err['msg']}{got_text}")
    lines.append(f"Fix these fields and call {tool_name} again.")
    return "\n".join(lines)


def format_exception(exc: BaseException) -> str:
    """Exception type, message and the innermost location - enough to act on, short enough to read."""
    frames = traceback.extract_tb(exc.__traceback__)
    where = f" (at {Path(frames[-1].filename).name}:{frames[-1].lineno} in {frames[-1].name})" if frames else ""
    return f"{type(exc).__name__}: {exc}{where}"


def _serialize(result: Any) -> str:
    if isinstance(result, BaseModel):
        return result.model_dump_json()
    if isinstance(result, (dict, list)):
        return json.dumps(result, default=str)
    return str(result)


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name {tool.name!r}")
        self._tools[tool.name] = tool

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    def execute(self, call: ToolCall) -> tuple[ToolResult, BaseModel | None]:
        """Run one call. Returns the result for the model and, on success, the validated input."""

        def error(message: str) -> tuple[ToolResult, None]:
            return ToolResult(call_id=call.id, name=call.name, content=message, is_error=True), None

        tool = self._tools.get(call.name)
        if tool is None:
            return error(f"Unknown tool {call.name!r}. Available tools: {', '.join(self.names)}.")
        if INVALID_JSON_KEY in call.arguments:
            return error(f"Your arguments for {call.name} were not valid JSON: "
                         f"{str(call.arguments[INVALID_JSON_KEY])[:120]!r}. Send a JSON object.")
        try:
            parsed = tool.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            return error(format_validation_error(exc, call.name))
        try:
            output = tool.fn(parsed)
        except Exception as exc:  # the tool's own bug or a bad lookup: report it, don't crash the run
            return error(f"{call.name} failed: {format_exception(exc)}")
        return ToolResult(call_id=call.id, name=call.name, content=_serialize(output)), parsed
