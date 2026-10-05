"""Reference solutions for trainer/exercises/m4_graphs.py. Compare with auditgate/agent/graph.py."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from trainer.exercises.m4_graphs import END, PAUSE, RESUME, Graph
from trainer.exercises.m4_graphs import Checkpointer as _Base


def route_after_tools(state: dict) -> str:
    if state["resolution"] is not None:
        return "policy"
    if state["had_error"] and state["repairs"] >= state["max_repairs"]:
        return "human_review"
    if state["had_error"]:
        return "repair"
    if state["steps"] >= state["max_steps"]:
        return "human_review"
    return "agent"


class Checkpointer(_Base):
    def save(self, thread_id: str, next_node: str, state: dict) -> None:
        self.conn.execute("INSERT INTO checkpoints (thread_id, next_node, state) VALUES (?, ?, ?)",
                          (thread_id, next_node, json.dumps(state)))
        self.conn.commit()

    def load(self, thread_id: str) -> tuple[str, dict] | None:
        row = self.conn.execute("SELECT next_node, state FROM checkpoints WHERE thread_id = ? "
                                "ORDER BY id DESC LIMIT 1", (thread_id,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None


def run(graph: Graph, thread_id: str, checkpointer: Checkpointer, *, start: str = "",
        state: dict | None = None, resume_value: Any = None, max_steps: int = 50) -> dict:
    if state is None:
        loaded = checkpointer.load(thread_id)
        if loaded is None:
            raise ValueError(f"nothing to resume for {thread_id!r}")
        node, state = loaded
        state[RESUME] = resume_value
    else:
        node = start
    for _ in range(max_steps):
        fn, nxt = graph[node]
        updates = fn(state)
        if PAUSE in updates:
            checkpointer.save(thread_id, node, state)
            return {"status": "paused", "state": state, "question": updates[PAUSE]}
        state = {**state, **updates}
        state.pop(RESUME, None)
        next_node = nxt(state) if callable(nxt) else nxt
        checkpointer.save(thread_id, next_node, state)
        if next_node == END:
            return {"status": "done", "state": state, "question": None}
        node = next_node
    raise RuntimeError("step budget exceeded")


def process_once(conn: sqlite3.Connection, event_id: str, action: Callable[[], dict]) -> tuple[dict, bool]:
    conn.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, result TEXT)")
    row = conn.execute("SELECT result FROM events WHERE event_id = ?", (event_id,)).fetchone()
    if row:
        return json.loads(row[0]), True
    result = action()
    conn.execute("INSERT INTO events VALUES (?, ?)", (event_id, json.dumps(result)))
    conn.commit()
    return result, False
