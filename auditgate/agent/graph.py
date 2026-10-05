"""The resolver agent as a LangGraph state graph, with SQLite checkpoints and human review.

    START ─► agent ─┬─► tools ─┬─► policy ─┬─► finalize ─► END          (clean approval)
              ▲     │          │           └─► human_review ─► finalize ─► END
              │     │          ├─► repair ──┬─► agent                  (tool error: fix and retry)
              │     │          │            └─► human_review            (too many repairs)
              │     │          ├─► agent                               (keep investigating)
              │     │          └─► human_review                        (step budget exhausted)
              │     └─► nudge ─► agent                                 (model answered without a tool)

Why a graph instead of the while-loop in loop.py?
  * Every edge is explicit and testable, including the cycles (repair loops).
  * A checkpointer saves state after every node, so a run can pause at
    ``human_review`` for days, survive a restart, and resume by thread id.
  * Irreversible side effects (queuing the vendor email) happen in ``finalize``,
    after the human, and are idempotent.

State is plain JSON-able data so it can be checkpointed. It includes the PII vault,
so the checkpoint database holds real values: keep it on this machine.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from auditgate.agent.models import Model, ModelTurn, ToolCall, ToolResult, TranscriptItem
from auditgate.agent.resolver import (
    SYSTEM_PROMPT,
    Resolution,
    Verdict,
    VendorStore,
    _task,
    build_registry,
    enforce_policy,
)
from auditgate.pipeline import AuditReport
from auditgate.security.sanitizer import Sanitizer, Vault


class ResolverState(TypedDict, total=False):
    report: dict[str, Any]
    transcript: list[dict[str, Any]]
    vault: dict[str, str]
    steps: int
    repairs: int
    resolution: dict[str, Any] | None
    violations: list[str]
    review_reason: str | None
    decision: dict[str, Any] | None
    outcome: str | None


# --------------------------------------------------------------------------- (de)serializing the transcript


def _encode(item: TranscriptItem) -> dict[str, Any]:
    if isinstance(item, str):
        return {"kind": "user", "text": item}
    if isinstance(item, ModelTurn):
        return {"kind": "assistant", "text": item.text,
                "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in item.tool_calls]}
    return {"kind": "results", "results": [r.__dict__ for r in item]}


def _decode(entry: dict[str, Any]) -> TranscriptItem:
    if entry["kind"] == "user":
        return entry["text"]
    if entry["kind"] == "assistant":
        return ModelTurn(text=entry["text"], tool_calls=[ToolCall(**c) for c in entry["tool_calls"]])
    return [ToolResult(**r) for r in entry["results"]]


def _vault(state: ResolverState) -> Vault:
    vault = Vault()
    for token, value in state.get("vault", {}).items():
        vault.token_to_value[token] = value
        vault.value_to_token[value] = token
        label = token.strip("[]").rsplit("_", 1)[0]
        vault.counters[label] = max(vault.counters.get(label, 0), int(token.strip("[]").rsplit("_", 1)[1]))
    return vault


# --------------------------------------------------------------------------- graph


def build_graph(model: Model, *, store: VendorStore | None = None, sanitizer: Sanitizer | None = None,
                max_steps: int = 8, max_repairs: int = 3, allowed_domains: tuple[str, ...] = (),
                checkpointer: Any = None, outbox: Outbox | None = None):
    registry = build_registry(store)
    sanitizer = sanitizer or Sanitizer(spacy_model=None)

    def agent(state: ResolverState) -> dict[str, Any]:
        transcript = [_decode(e) for e in state["transcript"]]
        turn = model.complete(SYSTEM_PROMPT, transcript, registry.specs())
        return {"transcript": state["transcript"] + [_encode(turn)], "steps": state.get("steps", 0) + 1}

    def after_agent(state: ResolverState) -> Literal["tools", "nudge"]:
        return "tools" if state["transcript"][-1]["tool_calls"] else "nudge"

    def nudge(state: ResolverState) -> dict[str, Any]:
        note = "You did not call a tool. Investigate with the tools, then call submit_resolution."
        return {"transcript": state["transcript"] + [{"kind": "user", "text": note}]}

    def tools(state: ResolverState) -> dict[str, Any]:
        vault = _vault(state)
        resolution = None
        results = []
        for call in _decode(state["transcript"][-1]).tool_calls:  # type: ignore[union-attr]
            real = ToolCall(call.id, call.name, vault.rehydrate(call.arguments))
            result, parsed = registry.execute(real)
            result.content = sanitizer.sanitize(result.content, vault).text
            if call.name == "submit_resolution" and parsed is not None:
                resolution = parsed.model_dump(mode="json")
            results.append(result)
        update: dict[str, Any] = {"vault": dict(vault.token_to_value)}
        if resolution is not None:
            update["resolution"] = resolution
        else:
            update["transcript"] = state["transcript"] + [_encode(results)]
        return update

    def after_tools(state: ResolverState) -> Literal["policy", "repair", "agent", "human_review"]:
        if state.get("resolution"):
            return "policy"
        if any(r["is_error"] for r in state["transcript"][-1]["results"]):
            return "repair"
        return "human_review" if state.get("steps", 0) >= max_steps else "agent"

    def repair(state: ResolverState) -> dict[str, Any]:
        repairs = state.get("repairs", 0) + 1
        update: dict[str, Any] = {"repairs": repairs}
        if repairs > max_repairs or state.get("steps", 0) >= max_steps:
            update["review_reason"] = f"Agent could not produce a valid call after {repairs} repair attempts."
        return update

    def after_repair(state: ResolverState) -> Literal["agent", "human_review"]:
        return "human_review" if state.get("review_reason") else "agent"

    def policy(state: ResolverState) -> dict[str, Any]:
        report = AuditReport.model_validate(state["report"])
        resolution, violations = enforce_policy(Resolution.model_validate(state["resolution"]), report,
                                                allowed_domains)
        return {"resolution": resolution.model_dump(mode="json"), "violations": violations}

    def after_policy(state: ResolverState) -> Literal["finalize", "human_review"]:
        verdict = Verdict(state["resolution"]["verdict"])
        return "finalize" if verdict is Verdict.APPROVE and not state.get("violations") else "human_review"

    def human_review(state: ResolverState) -> dict[str, Any]:
        # Everything before interrupt() re-runs on resume, so it must have no side effects.
        reason = state.get("review_reason")
        if reason is None and not state.get("resolution"):
            reason = f"Agent did not finish within {max_steps} steps."
        decision = interrupt({
            "question": "Approve this resolution?",
            "reason": reason or "Disputes and escalations always need a human.",
            "resolution": state.get("resolution"),
            "violations": state.get("violations", []),
        })
        return {"decision": decision, "review_reason": reason}

    def finalize(state: ResolverState) -> dict[str, Any]:
        decision = state.get("decision") or {"decision": "auto-approved"}
        resolution = state.get("resolution") or {}
        outcome = decision.get("decision", "rejected")
        if outcome == "approve" and resolution.get("verdict") == "dispute" and resolution.get("draft_email"):
            if outbox is not None:
                outbox.queue(thread_key(state), resolution["draft_email"])  # idempotent
            outcome = "dispute_email_queued"
        return {"outcome": outcome}

    graph = StateGraph(ResolverState)
    for name, fn in [("agent", agent), ("nudge", nudge), ("tools", tools), ("repair", repair),
                     ("policy", policy), ("human_review", human_review), ("finalize", finalize)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", after_agent)
    graph.add_edge("nudge", "agent")
    graph.add_conditional_edges("tools", after_tools)
    graph.add_conditional_edges("repair", after_repair)
    graph.add_conditional_edges("policy", after_policy)
    graph.add_edge("human_review", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


def thread_key(state: ResolverState) -> str:
    data = state["report"].get("data") or {}
    return str(data.get("invoice_number") or data.get("work_order_number") or data.get("project_name") or "unknown")


def initial_state(report: AuditReport, sanitizer: Sanitizer | None = None) -> ResolverState:
    sanitizer = sanitizer or Sanitizer(spacy_model=None)
    hidden = sanitizer.sanitize(_task(report))
    return {"report": report.model_dump(mode="json"), "transcript": [{"kind": "user", "text": hidden.text}],
            "vault": dict(hidden.vault.token_to_value), "steps": 0, "repairs": 0, "resolution": None,
            "violations": [], "review_reason": None, "decision": None, "outcome": None}


# --------------------------------------------------------------------------- durable storage


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def open_checkpointer(path: str | Path) -> SqliteSaver:
    saver = SqliteSaver(connect(path))
    saver.setup()
    return saver


class Outbox:
    """Queued vendor emails. One per document, no matter how many times finalize runs."""

    def __init__(self, path: str | Path) -> None:
        self.conn = connect(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS outbox (doc_key TEXT PRIMARY KEY, body TEXT, queued_at TEXT)")

    def queue(self, doc_key: str, body: str) -> bool:
        cur = self.conn.execute("INSERT OR IGNORE INTO outbox VALUES (?, ?, ?)",
                                (doc_key, body, datetime.now(timezone.utc).isoformat()))
        return cur.rowcount == 1

    def all(self) -> list[tuple[str, str]]:
        return [(k, b) for k, b, _ in self.conn.execute("SELECT * FROM outbox ORDER BY queued_at")]


class IdempotencyStore:
    """Remember processed event keys so a retried webhook does its work exactly once.

    Claim-then-work: the key is claimed with an atomic INSERT (the PRIMARY KEY makes a
    second claim fail), the work runs WITHOUT holding a database lock, then the result
    is stored. A duplicate delivery either replays the stored result or, if the first
    delivery is still working, reports "in_progress". A claim abandoned by a crash can
    be retaken after ``stale_after`` seconds.
    """

    def __init__(self, path: str | Path, stale_after: float = 600) -> None:
        self.conn = connect(path)
        self.stale_after = stale_after
        self.conn.execute("CREATE TABLE IF NOT EXISTS processed_events "
                          "(key TEXT PRIMARY KEY, result TEXT, claimed_at REAL NOT NULL)")

    def run_once(self, key: str, fn) -> tuple[Any, bool]:
        """Run fn() the first time `key` is seen; afterwards return the stored result. -> (result, replayed)"""
        now = time.time()
        claimed = self.conn.execute("INSERT OR IGNORE INTO processed_events VALUES (?, NULL, ?)",
                                    (key, now)).rowcount == 1
        if not claimed:
            result, claimed_at = self.conn.execute(
                "SELECT result, claimed_at FROM processed_events WHERE key = ?", (key,)).fetchone()
            if result is not None:
                return json.loads(result), True
            stale = self.conn.execute(  # retake only if nobody else retook it first
                "UPDATE processed_events SET claimed_at = ? WHERE key = ? AND result IS NULL AND claimed_at = ? "
                "AND claimed_at < ?", (now, key, claimed_at, now - self.stale_after)).rowcount == 1
            if not stale:
                return {"state": "in_progress", "key": key}, True
        try:
            result = fn()
        except BaseException:
            self.conn.execute("DELETE FROM processed_events WHERE key = ? AND result IS NULL", (key,))
            raise
        self.conn.execute("UPDATE processed_events SET result = ? WHERE key = ?", (json.dumps(result, default=str), key))
        return result, False


# --------------------------------------------------------------------------- service facade


class ResolutionService:
    """Start, inspect and resume durable resolver runs. One thread per document."""

    def __init__(self, model: Model, db_path: str | Path, **graph_kwargs: Any) -> None:
        self.db_path = Path(db_path)
        self.checkpointer = open_checkpointer(self.db_path)
        self.outbox = Outbox(self.db_path)
        self.events = IdempotencyStore(self.db_path)
        self.graph = build_graph(model, checkpointer=self.checkpointer, outbox=self.outbox, **graph_kwargs)

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def start(self, report: AuditReport, thread_id: str) -> dict[str, Any]:
        self.graph.invoke(initial_state(report), self._config(thread_id))
        return self.status(thread_id)

    def status(self, thread_id: str) -> dict[str, Any]:
        snapshot = self.graph.get_state(self._config(thread_id))
        if not snapshot.values:
            return {"thread_id": thread_id, "state": "unknown"}
        waiting = [i.value for task in snapshot.tasks for i in task.interrupts]
        values = snapshot.values
        return {
            "thread_id": thread_id,
            "state": "waiting_for_human" if waiting else ("completed" if values.get("outcome") else "running"),
            "review": waiting[0] if waiting else None,
            "resolution": values.get("resolution"),
            "violations": values.get("violations", []),
            "outcome": values.get("outcome"),
            "steps": values.get("steps", 0),
            "repairs": values.get("repairs", 0),
        }

    def resume(self, thread_id: str, decision: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        """Apply a human decision exactly once, however many times the webhook is delivered."""

        def apply() -> dict[str, Any]:
            if self.status(thread_id)["state"] != "waiting_for_human":
                return {"thread_id": thread_id, "error": "not waiting for a human decision"}
            self.graph.invoke(Command(resume=decision), self._config(thread_id))
            return self.status(thread_id)

        result, replayed = self.events.run_once(idempotency_key, apply)
        return result | {"replayed": replayed}
