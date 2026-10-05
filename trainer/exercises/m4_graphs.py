"""Module 4 - Build a tiny durable graph runtime yourself.

Before using LangGraph, build the four ideas it's made of, in plain Python + SQLite:

  1. a ROUTER (conditional edge) that picks the next node from the state
  2. a CHECKPOINTER that saves state after every node and loads the latest
  3. a RUNNER that walks the graph, can PAUSE for a human, and RESUME later
     (even from a brand-new process)
  4. an IDEMPOTENT handler, so a webhook delivered twice only acts once

    uv run python -m trainer.cli check M4

Then read auditgate/agent/graph.py - the same ideas, with LangGraph doing 2 and 3.
"""

from __future__ import annotations

import json  # noqa: F401 - you'll need it in exercises 2 and 4
import sqlite3
from collections.abc import Callable
from typing import Any

END = "END"
PAUSE = "__pause__"     # a node returns {PAUSE: <question for the human>} to stop and wait
RESUME = "__resume__"   # on resume, the human's answer is put in state[RESUME] and the node runs again

# A graph maps node name -> (node function, next).
#   node function: takes the state dict, returns a dict of updates to merge into it
#   next: either a node name (str) or a router function(state) -> node name
Graph = dict[str, tuple[Callable[[dict], dict], "str | Callable[[dict], str]"]]


# --------------------------------------------------------------------------
# Exercise 1 - a conditional edge
# --------------------------------------------------------------------------


def route_after_tools(state: dict) -> str:
    """Decide where the resolver goes after running tools. Check in this order:

    1. state["resolution"] is not None                       -> "policy"
    2. state["had_error"] is True and
       state["repairs"] >= state["max_repairs"]              -> "human_review"
    3. state["had_error"] is True                            -> "repair"
    4. state["steps"] >= state["max_steps"]                  -> "human_review"
    5. otherwise                                             -> "agent"
    """
    raise NotImplementedError("Exercise 1: route_after_tools")


# --------------------------------------------------------------------------
# Exercise 2 - a checkpointer
# --------------------------------------------------------------------------


class Checkpointer:
    """Saves (next_node, state) for a thread after every step. The table is created for you."""

    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS checkpoints ("
                          "id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, next_node TEXT, state TEXT)")

    def save(self, thread_id: str, next_node: str, state: dict) -> None:
        """Insert a new row (keep history - never overwrite) and commit.

        Hint: self.conn.execute("INSERT INTO checkpoints (thread_id, next_node, state) VALUES (?, ?, ?)",
                                (thread_id, next_node, json.dumps(state)))
              then self.conn.commit()
        """
        raise NotImplementedError("Exercise 2a: Checkpointer.save")

    def load(self, thread_id: str) -> tuple[str, dict] | None:
        """Return (next_node, state) from the NEWEST row for this thread, or None if there are none.

        Hint: ORDER BY id DESC LIMIT 1, and json.loads the state.
        """
        raise NotImplementedError("Exercise 2b: Checkpointer.load")


# --------------------------------------------------------------------------
# Exercise 3 - the runner, with pause and resume
# --------------------------------------------------------------------------


def run(graph: Graph, thread_id: str, checkpointer: Checkpointer, *, start: str = "",
        state: dict | None = None, resume_value: Any = None, max_steps: int = 50) -> dict:
    """Run a graph until END or a pause. Returns {"status": "done"|"paused", "state": ..., "question": ...}.

    Starting fresh:  run(graph, "t1", cp, start="first_node", state={...})
    Resuming:        run(graph, "t1", cp, resume_value="approve")   # loads the checkpoint itself

    Algorithm:
      - If resuming (state is None): load the checkpoint -> (node, state); put resume_value in state[RESUME].
        Otherwise: node = start.
      - Loop (at most max_steps times, then raise RuntimeError):
          updates = node function(state)
          if PAUSE in updates:
              save checkpoint with next_node = THIS SAME node (it re-runs on resume)
              return {"status": "paused", "state": state, "question": updates[PAUSE]}
          merge updates into state; remove RESUME from state if present
          work out the next node (call it if it's a router function)
          save checkpoint with next_node = that next node
          if it's END: return {"status": "done", "state": state, "question": None}
          node = next node
    """
    raise NotImplementedError("Exercise 3: run")


# --------------------------------------------------------------------------
# Exercise 4 - exactly-once webhook handling
# --------------------------------------------------------------------------


def process_once(conn: sqlite3.Connection, event_id: str, action: Callable[[], dict]) -> tuple[dict, bool]:
    """Run action() only the first time event_id is seen. Return (result, was_duplicate).

    1. Create the table if needed:  events (event_id TEXT PRIMARY KEY, result TEXT)
    2. If event_id is already in the table: return (json.loads(stored result), True)
    3. Otherwise: result = action(); insert (event_id, json.dumps(result)); commit; return (result, False)
    """
    raise NotImplementedError("Exercise 4: process_once")
