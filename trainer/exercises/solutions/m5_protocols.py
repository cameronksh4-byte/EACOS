"""Reference solutions for trainer/exercises/m5_protocols.py."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from trainer.exercises.m5_protocols import McpTool, Span
from trainer.exercises.m5_protocols import MiniTracer as _Base


def prompt_version(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


class MiniTracer(_Base):
    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        s = Span(name=name, parent=self._stack[-1].name if self._stack else None, attributes=dict(attributes))
        self._stack.append(s)
        start = time.perf_counter()
        try:
            yield s
        finally:
            self._stack.pop()
            s.duration_ms = (time.perf_counter() - start) * 1000
            self.finished.append(s)


def safe_attributes(attributes: dict[str, Any], sensitive_keys: set[str], max_chars: int = 50) -> dict[str, Any]:
    safe = {}
    for key, value in attributes.items():
        if key in sensitive_keys:
            safe[key] = "[REDACTED]"
        elif isinstance(value, str) and len(value) > max_chars:
            safe[key] = value[:max_chars] + "…"
        else:
            safe[key] = value
    return safe


def cost_usd(input_tokens: int, output_tokens: int, price_in_per_million: str, price_out_per_million: str) -> Decimal:
    total = input_tokens * Decimal(price_in_per_million) + output_tokens * Decimal(price_out_per_million)
    return (total / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def handle_mcp_request(request: dict[str, Any], tools: list[McpTool]) -> dict[str, Any]:
    base = {"jsonrpc": "2.0", "id": request.get("id")}
    method = request.get("method")
    if method == "tools/list":
        listing = [{"name": t.name, "description": t.description, "inputSchema": t.input_model.model_json_schema()}
                   for t in tools]
        return base | {"result": {"tools": listing}}
    if method == "tools/call":
        params = request.get("params", {})
        tool = next((t for t in tools if t.name == params.get("name")), None)
        if tool is None:
            return base | {"error": {"code": -32602, "message": f"Unknown tool: {params.get('name')}"}}
        try:
            parsed = tool.input_model.model_validate(params.get("arguments", {}))
        except ValidationError as exc:
            return base | {"result": {"content": [{"type": "text", "text": str(exc)}], "isError": True}}
        return base | {"result": {"content": [{"type": "text", "text": tool.fn(parsed)}], "isError": False}}
    return base | {"error": {"code": -32601, "message": "Method not found"}}
