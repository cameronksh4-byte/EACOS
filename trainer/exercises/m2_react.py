"""Module 2 - Build a ReAct agent loop from scratch.

No frameworks, no SDKs: just the four pieces every agent is made of. The tests
use a fake "model" (a plain function), so you don't need an API key.

    uv run python -m trainer.cli check M2

When all four pass, read auditgate/agent/loop.py and tools.py - the production
version of what you just wrote, plus PII handling and step tracing.

The building blocks are defined for you below. Don't change them.
"""

from __future__ import annotations

import json  # noqa: F401 - you'll want it in exercise 3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]       # the Pydantic model that describes valid arguments
    fn: Callable[[BaseModel], Any]     # the local Python function to run


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class ModelTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ReactResult:
    answer: BaseModel | None           # validated input of the final tool, or None
    steps: int                         # how many times the model was called
    stopped: str                       # "answered" or "budget"


# A "model" is any function that takes the transcript and returns a ModelTurn.
# Transcript items: the task (str), then alternating ModelTurn and list[ToolResult].
ModelFn = Callable[[list[Any]], ModelTurn]


# --------------------------------------------------------------------------
# Exercise 1 - describe a tool to the model
# --------------------------------------------------------------------------


def tool_schema(tool: Tool) -> dict[str, Any]:
    """Return the tool definition a model API expects:

        {"name": ..., "description": ..., "input_schema": <JSON Schema>}

    Hint: Pydantic gives you the JSON Schema: tool.input_model.model_json_schema()
    """
    raise NotImplementedError("Exercise 1: tool_schema")


# --------------------------------------------------------------------------
# Exercise 2 - turn a validation error into feedback the model can act on
# --------------------------------------------------------------------------


def format_validation_error(exc: ValidationError, tool_name: str) -> str:
    """Build a short message that names the tool and, for EVERY error, the field and the problem.

    Example output (exact wording is up to you):

        Invalid arguments for line_total:
        - quantity: Input should be greater than 0
        Fix these fields and call line_total again.

    Hint: exc.errors() is a list of dicts with "loc" (a tuple of field names) and "msg".
    """
    raise NotImplementedError("Exercise 2: format_validation_error")


# --------------------------------------------------------------------------
# Exercise 3 - execute one tool call safely
# --------------------------------------------------------------------------


def execute_tool(call: ToolCall, tools: dict[str, Tool]) -> tuple[ToolResult, BaseModel | None]:
    """Run a tool call and NEVER raise. Return (result, validated_input_or_None).

    - Unknown tool name   -> is_error=True, and the message lists the available tool names
    - Invalid arguments   -> is_error=True, content from format_validation_error()
    - The tool raises     -> is_error=True, content includes the exception type and message
    - Success             -> is_error=False, content is the tool's return value as a string
                             (use json.dumps for dicts and lists), plus the validated input

    Hint: tool.input_model.model_validate(call.arguments) raises ValidationError on bad input.
    """
    raise NotImplementedError("Exercise 3: execute_tool")


# --------------------------------------------------------------------------
# Exercise 4 - the loop
# --------------------------------------------------------------------------


def run_react(model: ModelFn, tools: dict[str, Tool], task: str, final_tool: str,
              max_steps: int = 3) -> ReactResult:
    """The agent loop:

        transcript = [task]
        repeat at most max_steps times:
            turn = model(transcript); append turn
            run every tool call in turn with execute_tool()
            if the final_tool call succeeded -> return ReactResult(answer, steps, "answered")
            append the list of results (errors included, so the model can fix itself)
        -> return ReactResult(None, max_steps, "budget")

    If a turn has no tool calls, append a reminder string telling the model to call a tool.
    """
    raise NotImplementedError("Exercise 4: run_react")

