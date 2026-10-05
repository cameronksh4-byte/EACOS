"""Module 5 - Observability and tool protocols, by hand.

  1-3: the core of tracing - versioned prompts, nested timed spans, PII-safe attributes
  4:   cost accounting from token counts
  5:   speak raw MCP: answer JSON-RPC "tools/list" and "tools/call" messages

    uv run python -m trainer.cli check M5

Then read auditgate/observability.py and auditgate/mcp_server.py, where
OpenTelemetry and the official MCP SDK do the same jobs.
"""

from __future__ import annotations

import hashlib  # noqa: F401 - exercise 1
import json  # noqa: F401 - exercise 5
import time  # noqa: F401 - exercise 2
from collections.abc import Callable, Iterator
from contextlib import contextmanager  # noqa: F401 - exercise 2
from dataclasses import dataclass, field
from decimal import Decimal  # noqa: F401 - exercise 4
from typing import Any

from pydantic import BaseModel, ValidationError  # noqa: F401 - exercise 5

# --------------------------------------------------------------------------
# Exercise 1 - version your prompts
# --------------------------------------------------------------------------


def prompt_version(prompt: str) -> str:
    """Return the first 12 hex characters of the SHA-256 of the prompt (UTF-8 encoded).

    Any edit to a prompt changes its version, so every trace records which prompt ran.
    Hint: hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    """
    raise NotImplementedError("Exercise 1: prompt_version")


# --------------------------------------------------------------------------
# Exercise 2 - nested, timed spans
# --------------------------------------------------------------------------


@dataclass
class Span:
    name: str
    parent: str | None
    attributes: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0


class MiniTracer:
    """Records spans. Use it like OpenTelemetry:

        tracer = MiniTracer()
        with tracer.span("agent.run", model="x") as run:
            with tracer.span("agent.step", index=1):
                ...
            run.attributes["status"] = "completed"

    After both blocks, tracer.finished == [Span("agent.step", parent="agent.run", ...),
                                           Span("agent.run", parent=None, ...)]
    (a span is added to `finished` when its block ENDS, so children come first).
    """

    def __init__(self) -> None:
        self.finished: list[Span] = []
        self._stack: list[Span] = []   # spans currently open, innermost last

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        """Create a Span whose parent is the innermost open span's name (or None), push it,
        yield it, and when the block ends (even if it raised!) pop it, set duration_ms
        and append it to self.finished.

        Hint: start = time.perf_counter() ... try: yield s  finally: ...
        """
        raise NotImplementedError("Exercise 2: MiniTracer.span")
        yield  # pragma: no cover - keeps this a generator until you implement it


# --------------------------------------------------------------------------
# Exercise 3 - attributes that never leak PII
# --------------------------------------------------------------------------


def safe_attributes(attributes: dict[str, Any], sensitive_keys: set[str], max_chars: int = 50) -> dict[str, Any]:
    """Return a copy that is safe to store in a trace:
      - values of keys in sensitive_keys become "[REDACTED]"
      - other string values longer than max_chars are cut to max_chars characters followed by "…"
      - everything else is unchanged
    """
    raise NotImplementedError("Exercise 3: safe_attributes")


# --------------------------------------------------------------------------
# Exercise 4 - what did this run cost?
# --------------------------------------------------------------------------


def cost_usd(input_tokens: int, output_tokens: int, price_in_per_million: str, price_out_per_million: str) -> Decimal:
    """Dollar cost, using Decimal (never float for money), rounded to 6 decimal places.

    cost = (input_tokens * price_in + output_tokens * price_out) / 1,000,000
    Hint: Decimal(price) and .quantize(Decimal("0.000001"))
    """
    raise NotImplementedError("Exercise 4: cost_usd")


# --------------------------------------------------------------------------
# Exercise 5 - a tiny MCP server
# --------------------------------------------------------------------------


@dataclass
class McpTool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[BaseModel], str]


def handle_mcp_request(request: dict[str, Any], tools: list[McpTool]) -> dict[str, Any]:
    """Answer one JSON-RPC 2.0 request the way an MCP server does.

    Every response is {"jsonrpc": "2.0", "id": <the request's id>, ...} plus ONE of:

    method "tools/list"  -> "result": {"tools": [{"name": ..., "description": ...,
                                                   "inputSchema": <the input model's JSON Schema>}, ...]}
    method "tools/call"  -> params are {"name": ..., "arguments": {...}}
        - known tool, valid arguments  -> "result": {"content": [{"type": "text", "text": fn(parsed)}],
                                                     "isError": False}
        - arguments fail validation    -> "result": {"content": [{"type": "text", "text": <error message>}],
                                                     "isError": True}
          (a tool error is a normal RESULT, so the model can read it and retry)
        - unknown tool name            -> "error": {"code": -32602, "message": "Unknown tool: <name>"}
    any other method                   -> "error": {"code": -32601, "message": "Method not found"}
    """
    raise NotImplementedError("Exercise 5: handle_mcp_request")
