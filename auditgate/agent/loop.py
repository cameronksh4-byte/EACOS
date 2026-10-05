"""The ReAct loop, by hand.

    while steps < max_steps:
        turn = model(transcript)                 # reason + choose tools
        if no tool calls: nudge and continue     # the model must act, not chat
        results = [execute(call) for call in turn.tool_calls]
        if the final tool succeeded: done        # validated answer
        transcript += [turn, results]            # errors included -> self-correction
    -> budget exhausted: hand to a human

PII boundary: when a sanitizer is supplied, the model only ever sees placeholders.
Tool arguments are re-hydrated before a tool runs (tools work on real data, locally),
and tool results are re-sanitized before going back into the transcript.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from opentelemetry.trace import Tracer
from pydantic import BaseModel

from auditgate import observability as obs
from auditgate.agent.models import Model, ModelTurn, ToolCall, ToolResult, TranscriptItem
from auditgate.agent.tools import ToolRegistry
from auditgate.security.sanitizer import Sanitizer, Vault

RunStatus = Literal["completed", "budget_exhausted"]


@dataclass
class Step:
    """One model call and the tool calls it made. Arguments/results are as the model saw them (sanitized)."""

    index: int
    text: str
    tool_calls: list[ToolCall]
    results: list[ToolResult]
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def had_error(self) -> bool:
        return any(r.is_error for r in self.results)


@dataclass
class AgentRun:
    status: RunStatus
    final: BaseModel | None
    steps: list[Step] = field(default_factory=list)
    transcript: list[TranscriptItem] = field(default_factory=list)

    @property
    def repairs(self) -> int:
        """Steps where the model had to correct an error it made."""
        return sum(step.had_error for step in self.steps)

    @property
    def tokens(self) -> tuple[int, int]:
        return sum(s.input_tokens for s in self.steps), sum(s.output_tokens for s in self.steps)


def run_agent(
    model: Model,
    registry: ToolRegistry,
    *,
    system: str,
    task: str,
    final_tool: str,
    max_steps: int = 6,
    sanitizer: Sanitizer | None = None,
    otel_tracer: Tracer | None = None,
    vault: Vault | None = None,
) -> AgentRun:
    if final_tool not in registry.names:
        raise ValueError(f"final tool {final_tool!r} is not registered")
    vault = vault if vault is not None else Vault()
    hide = (lambda text: sanitizer.sanitize(text, vault).text) if sanitizer else (lambda text: text)
    tracer = otel_tracer or obs.tracer()

    transcript: list[TranscriptItem] = [hide(task)]
    run = AgentRun(status="budget_exhausted", final=None, transcript=transcript)

    with tracer.start_as_current_span("agent.run", attributes={
        "agent.model": model.name, "agent.prompt_version": obs.prompt_version(system),
        "agent.max_steps": max_steps, "agent.final_tool": final_tool,
    }) as run_span:
        for index in range(1, max_steps + 1):
            with tracer.start_as_current_span("agent.step", attributes={"step.index": index}) as step_span:
                started = time.perf_counter()
                turn: ModelTurn = model.complete(system, transcript, registry.specs())
                latency = (time.perf_counter() - started) * 1000
                transcript.append(turn)

                results: list[ToolResult] = []
                for call in turn.tool_calls:
                    with tracer.start_as_current_span(f"tool.{call.name}") as tool_span:
                        real_call = ToolCall(id=call.id, name=call.name, arguments=vault.rehydrate(call.arguments))
                        result, parsed = registry.execute(real_call)
                        result.content = hide(result.content)
                        # Argument NAMES only and a sanitized excerpt: traces must never hold raw PII.
                        tool_span.set_attributes({"tool.is_error": result.is_error,
                                                  "tool.argument_names": sorted(call.arguments),
                                                  "tool.result_excerpt": obs.truncate(result.content)})
                    if call.name == final_tool and parsed is not None:
                        run.final = parsed
                    results.append(result)

                step_span.set_attributes({"step.latency_ms": round(latency, 1),
                                          "step.input_tokens": turn.input_tokens,
                                          "step.output_tokens": turn.output_tokens,
                                          "step.tools": [c.name for c in turn.tool_calls]})
            run.steps.append(Step(index=index, text=turn.text, tool_calls=turn.tool_calls, results=results,
                                  latency_ms=round(latency, 1), input_tokens=turn.input_tokens,
                                  output_tokens=turn.output_tokens))
            if run.final is not None:
                run.status = "completed"
                break
            if results:
                transcript.append(results)
            else:
                transcript.append(f"You did not call a tool. Use the tools to investigate, "
                                  f"then call {final_tool} with your answer.")

        tokens_in, tokens_out = run.tokens
        run_span.set_attributes({"agent.status": run.status, "agent.steps": len(run.steps),
                                 "agent.repairs": run.repairs, "agent.input_tokens": tokens_in,
                                 "agent.output_tokens": tokens_out})
        cost = obs.cost_usd(model.name, tokens_in, tokens_out)
        if cost is not None:
            run_span.set_attribute("agent.cost_usd", float(cost))
    return run
