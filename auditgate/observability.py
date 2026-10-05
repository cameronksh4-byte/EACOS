"""OpenTelemetry tracing for agent runs - an audit trail of every decision.

A *trace* is one agent run; *spans* are its timed steps, nested parent/child:

    agent.run            model, prompt_version, status, steps, repairs, tokens, cost
    ├── agent.step       index, latency, tokens, tool names
    │   ├── tool.lookup_vendor_history   is_error, argument keys
    │   └── tool.find_purchase_orders
    └── agent.step ...
    human.decision       decision, approver   (recorded when a person resumes a run)

Rules:
  * Span data is PII-safe: we record argument *names* and sanitized excerpts, never raw values.
  * With no exporter configured, OpenTelemetry is a no-op - tracing costs nothing.
  * Prices aren't hard-coded (they change). Set AUDITGATE_PRICES="model=in/out,..." in
    dollars per million tokens, from your provider's current price list, to get cost per run.

    configure("console")    # print spans as they finish (local debugging)
    configure("otlp")       # send to a collector (Jaeger, Grafana Tempo, Honeycomb...); needs
                            # `uv add opentelemetry-exporter-otlp`
"""

from __future__ import annotations

import hashlib
import os
from decimal import Decimal
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

TRACER_NAME = "auditgate"
MAX_ATTR_CHARS = 200


def tracer(provider: TracerProvider | None = None) -> trace.Tracer:
    return (provider or trace.get_tracer_provider()).get_tracer(TRACER_NAME)


def configure(exporter: str = "console") -> TracerProvider:
    """Install a global tracer provider. Call once at startup."""
    provider = TracerProvider()
    if exporter == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    elif exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # optional extra
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    else:
        raise ValueError("exporter must be 'console' or 'otlp'")
    trace.set_tracer_provider(provider)
    return provider


def prompt_version(prompt: str) -> str:
    """A short, stable fingerprint: any edit to the prompt changes it, so every trace shows which prompt ran."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def truncate(value: str, limit: int = MAX_ATTR_CHARS) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def load_prices(raw: str | None = None) -> dict[str, tuple[Decimal, Decimal]]:
    """Parse "model-a=3/15,model-b=0.8/4" (USD per million input/output tokens)."""
    raw = os.environ.get("AUDITGATE_PRICES", "") if raw is None else raw
    prices = {}
    for item in filter(None, (p.strip() for p in raw.split(","))):
        model, _, pair = item.partition("=")
        price_in, _, price_out = pair.partition("/")
        prices[model.strip()] = (Decimal(price_in), Decimal(price_out))
    return prices


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             prices: dict[str, tuple[Decimal, Decimal]] | None = None) -> Decimal | None:
    """Dollar cost of a run, or None if no price is configured for this model."""
    prices = load_prices() if prices is None else prices
    key = model.split(":", 1)[-1]  # "anthropic:claude-x" -> "claude-x"
    if key not in prices:
        return None
    price_in, price_out = prices[key]
    return ((price_in * input_tokens + price_out * output_tokens) / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def render_trail(spans: list[ReadableSpan]) -> list[str]:
    """Turn finished spans into plain sentences a business owner can read."""
    lines = []
    for span in sorted(spans, key=lambda s: s.start_time or 0):
        a = dict(span.attributes or {})
        ms = ((span.end_time or 0) - (span.start_time or 0)) / 1e6
        if span.name == "agent.run":
            lines.append(f"Run finished: {a.get('agent.status')} after {a.get('agent.steps')} steps "
                         f"({a.get('agent.repairs', 0)} self-corrections), prompt {a.get('agent.prompt_version')}.")
        elif span.name == "agent.step":
            tools = a.get("step.tools") or ()
            lines.append(f"Step {a.get('step.index')} ({ms:.0f} ms): "
                         + (f"used {', '.join(tools)}" if tools else "answered without a tool"))
        elif span.name.startswith("tool.") and a.get("tool.is_error"):
            lines.append(f"  {span.name[5:]} returned an error; the agent was asked to fix its call.")
        elif span.name == "human.decision":
            lines.append(f"Human decision: {a.get('human.decision')} by {a.get('human.approver')}.")
    return lines
