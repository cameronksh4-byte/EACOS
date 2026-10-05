"""Provider-neutral model layer, written against the raw SDKs (no agent frameworks).

The agent loop keeps its own transcript in neutral types. Each adapter translates
that transcript into its provider's message format on every call. That keeps the
loop identical for Anthropic, OpenAI, a local Ollama server, or a scripted test model.

Transcript items:
    str               - a user message
    ModelTurn         - what the model said / which tools it called
    list[ToolResult]  - results of the tool calls from the previous ModelTurn
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, Union


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass
class ModelTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


TranscriptItem = Union[str, ModelTurn, list[ToolResult]]
INVALID_JSON_KEY = "__invalid_json__"


class Model(Protocol):
    name: str

    def complete(self, system: str, transcript: list[TranscriptItem], tools: list[ToolSpec]) -> ModelTurn: ...


# --------------------------------------------------------------------------- Anthropic


def _merge_same_role(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic expects user/assistant turns to alternate; merge neighbours with the same role."""
    merged: list[dict[str, Any]] = []
    for msg in messages:
        content = msg["content"] if isinstance(msg["content"], list) else [{"type": "text", "text": msg["content"]}]
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"].extend(content)
        else:
            merged.append({"role": msg["role"], "content": list(content)})
    return merged


class AnthropicModel:
    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 2048, client: Any = None) -> None:
        if client is None:
            from anthropic import Anthropic

            client = Anthropic(api_key=api_key)
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.name = f"anthropic:{model}"

    @staticmethod
    def to_messages(transcript: list[TranscriptItem]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for item in transcript:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
            elif isinstance(item, ModelTurn):
                blocks: list[dict[str, Any]] = [{"type": "text", "text": item.text}] if item.text else []
                blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                           for c in item.tool_calls]
                messages.append({"role": "assistant", "content": blocks})
            else:
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
                    for r in item
                ]})
        return _merge_same_role(messages)

    def complete(self, system: str, transcript: list[TranscriptItem], tools: list[ToolSpec]) -> ModelTurn:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=self.to_messages(transcript),
            tools=[{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools],
        )
        turn = ModelTurn(input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)
        for block in response.content:
            if block.type == "text":
                turn.text += block.text
            elif block.type == "tool_use":
                turn.tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return turn


# --------------------------------------------------------------------------- OpenAI-compatible


class OpenAICompatibleModel:
    """OpenAI's Chat Completions format - also spoken by Ollama, LM Studio and vLLM."""

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None, client: Any = None) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url)
        self.client = client
        self.model = model
        self.name = f"openai-compatible:{model}"

    @staticmethod
    def to_messages(system: str, transcript: list[TranscriptItem]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for item in transcript:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
            elif isinstance(item, ModelTurn):
                msg: dict[str, Any] = {"role": "assistant", "content": item.text or None}
                if item.tool_calls:
                    msg["tool_calls"] = [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.arguments, default=str)}}
                        for c in item.tool_calls
                    ]
                messages.append(msg)
            else:
                messages += [{"role": "tool", "tool_call_id": r.call_id, "content": r.content} for r in item]
        return messages

    def complete(self, system: str, transcript: list[TranscriptItem], tools: list[ToolSpec]) -> ModelTurn:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self.to_messages(system, transcript),
            tools=[{"type": "function", "function": {"name": t.name, "description": t.description,
                                                     "parameters": t.input_schema}} for t in tools],
        )
        message = response.choices[0].message
        usage = getattr(response, "usage", None)
        turn = ModelTurn(text=message.content or "",
                         input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                         output_tokens=getattr(usage, "completion_tokens", 0) or 0)
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                # Don't crash: hand the raw text to the tool executor, which reports it back to the model.
                arguments = {INVALID_JSON_KEY: call.function.arguments}
            turn.tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        return turn


# --------------------------------------------------------------------------- scripted (tests & offline demo)


class ScriptedModel:
    """Replays a fixed list of turns. Each entry is a ModelTurn or a function of the transcript."""

    def __init__(self, turns: list[ModelTurn | Callable[[list[TranscriptItem]], ModelTurn]], name: str = "scripted"):
        self.turns = list(turns)
        self.name = name
        self.seen: list[list[TranscriptItem]] = []  # what the model was shown on each call

    def complete(self, system: str, transcript: list[TranscriptItem], tools: list[ToolSpec]) -> ModelTurn:
        self.seen.append(list(transcript))
        if not self.turns:
            return ModelTurn(text="(script exhausted)")
        turn = self.turns.pop(0)
        return turn(transcript) if callable(turn) else turn
