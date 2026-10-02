"""
LESSON 3 — Memory (checkpointers) and Human-in-the-Loop (interrupts)
=====================================================================

Run it:   python 03_memory_and_human_in_the_loop.py

THE BIG IDEA
------------
So far every .invoke() started from scratch. Two features change that:

  1. CHECKPOINTER — after every node, LangGraph saves a snapshot of the
     state. Snapshots are grouped by a `thread_id` (think: one conversation,
     one ticket, one user session). Run again with the same thread_id and
     the graph remembers where it left off.

  2. interrupt() — a node can PAUSE the graph and wait for a human.
     Because the state is checkpointed, the pause can last seconds or days.
     You resume by invoking again with `Command(resume=<the human's answer>)`.

Our example: an expense-approval workflow.

   START ► check_amount ──► (small) ─────────────────► record ► END
                        └─► (large) ► manager_approval ─┘
                                        ⏸  waits for a human
"""

from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict):
    employee: str
    amount: float
    status: str


AUTO_APPROVE_LIMIT = 100.0


def check_amount(state: State) -> dict:
    print(f"  [check_amount] {state['employee']} requests ${state['amount']:.2f}")
    return {"status": "pending"}


def route(state: State) -> Literal["manager_approval", "record"]:
    return "manager_approval" if state["amount"] > AUTO_APPROVE_LIMIT else "record"


def manager_approval(state: State) -> dict:
    # -----------------------------------------------------------------------
    # interrupt() pauses the graph RIGHT HERE.
    #   * The value you pass in is shown to the caller (what to ask the human).
    #   * When resumed, interrupt() RETURNS whatever the human sent back.
    #
    # ⚠️ GOTCHA: on resume, the node re-runs FROM THE TOP. So keep code
    #    before interrupt() side-effect free (no emails, no DB writes!).
    # -----------------------------------------------------------------------
    decision = interrupt(
        {
            "question": "Approve this expense?",
            "employee": state["employee"],
            "amount": state["amount"],
        }
    )
    print(f"  [manager_approval] human said: {decision!r}")
    return {"status": "approved" if decision == "yes" else "rejected"}


def record(state: State) -> dict:
    status = "approved" if state["status"] == "pending" else state["status"]
    print(f"  [record] saving expense as {status.upper()}")
    return {"status": status}


builder = StateGraph(State)
builder.add_node("check_amount", check_amount)
builder.add_node("manager_approval", manager_approval)
builder.add_node("record", record)
builder.add_edge(START, "check_amount")
builder.add_conditional_edges("check_amount", route)
builder.add_edge("manager_approval", "record")
builder.add_edge("record", END)

# 💡 The checkpointer is passed at COMPILE time.
#    InMemorySaver is for learning/tests. In production swap in
#    SqliteSaver or PostgresSaver (pip install langgraph-checkpoint-postgres)
#    so state survives restarts.
graph = builder.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":
    # --- Case A: a small expense flows straight through -----------------
    print("Case A: $40 lunch")
    config_a = {"configurable": {"thread_id": "expense-A"}}
    result = graph.invoke({"employee": "Ruth", "amount": 40.0}, config_a)
    print(f"  -> final status: {result['status']}\n")

    # --- Case B: a large expense pauses for a human ---------------------
    print("Case B: $950 conference ticket")
    config_b = {"configurable": {"thread_id": "expense-B"}}
    result = graph.invoke({"employee": "Boaz", "amount": 950.0}, config_b)

    # When a graph is interrupted, the result contains "__interrupt__".
    pending = result["__interrupt__"][0].value
    print(f"  ⏸  PAUSED. Graph is asking: {pending}")

    # We can inspect the saved checkpoint while it's paused:
    snapshot = graph.get_state(config_b)
    print(f"  Next node waiting to run: {snapshot.next}")

    # ... time passes, a manager clicks "approve" in some UI ...
    answer = "yes"
    print(f"\n  ▶  Resuming with the manager's answer: {answer!r}")
    result = graph.invoke(Command(resume=answer), config_b)  # SAME thread_id!
    print(f"  -> final status: {result['status']}\n")

    # --- Time travel: every checkpoint is kept ---------------------------
    print("Checkpoint history for expense-B (newest first):")
    for snap in graph.get_state_history(config_b):
        print(f"  next={snap.next!s:<24} status={snap.values.get('status')}")

# ---------------------------------------------------------------------------
# ✅ CHECK YOUR UNDERSTANDING
#   1. Resume Case B with "no" instead. What's the final status?
#   2. Resume Case B using a DIFFERENT thread_id. What happens, and why?
#   3. Compile WITHOUT a checkpointer and run Case B. Why does interrupt()
#      need one?
# ---------------------------------------------------------------------------
