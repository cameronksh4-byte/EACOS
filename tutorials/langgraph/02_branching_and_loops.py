"""
LESSON 2 — Decisions and loops: conditional edges + reducers
=============================================================

Run it:   python 02_branching_and_loops.py

THE BIG IDEA
------------
Real workflows make decisions. In LangGraph, a CONDITIONAL EDGE is a
function that looks at the state and returns the NAME of the next node.

We'll build a tiny "essay reviewer" that drafts, reviews, and keeps
revising until the draft is good enough (or we run out of tries):

            ┌──────────── revise ◄──────┐
            ▼                           │ (not good enough)
   START ► draft ► review ──► route? ───┤
                                        │ (good, or too many tries)
                                        └──► publish ► END

New concepts:
  * add_conditional_edges  — branch based on state
  * Loops                  — just an edge pointing backwards!
  * REDUCERS               — control HOW an update merges into state
"""

import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph


# ---------------------------------------------------------------------------
# STATE with a REDUCER
# ---------------------------------------------------------------------------
# By default, when a node returns {"key": value}, the old value is
# OVERWRITTEN. But sometimes you want to ACCUMULATE instead.
#
# `Annotated[list[str], operator.add]` tells LangGraph:
#     "when a node returns {'history': [...]}, ADD it to the existing list"
#
# 💡 TEACHING NOTE: This is exactly how chat history works in LangGraph —
#    each node appends messages rather than replacing the conversation.
class State(TypedDict):
    topic: str
    draft: str
    score: int
    attempts: int
    history: Annotated[list[str], operator.add]   # <- reducer!


MAX_ATTEMPTS = 3
PASSING_SCORE = 8


# ---------------------------------------------------------------------------
# NODES
# ---------------------------------------------------------------------------
# In a real app these would call an LLM. Here they're deterministic so you
# can focus on the *graph mechanics* (and run this with no API key).
def draft(state: State) -> dict:
    text = f"Some thoughts on {state['topic']}."
    return {"draft": text, "attempts": 0, "history": [f"drafted: {text!r}"]}


def review(state: State) -> dict:
    # Pretend scoring: longer drafts score higher.
    score = min(10, len(state["draft"]) // 8)
    return {"score": score, "history": [f"reviewed: score={score}"]}


def revise(state: State) -> dict:
    improved = state["draft"] + " Here is more detail and a clear example."
    return {
        "draft": improved,
        "attempts": state["attempts"] + 1,
        "history": [f"revised (attempt {state['attempts'] + 1})"],
    }


def publish(state: State) -> dict:
    return {"history": [f"published with score {state['score']}"]}


# ---------------------------------------------------------------------------
# THE ROUTER — a conditional edge function
# ---------------------------------------------------------------------------
# It reads the state and returns the name of the next node.
# The `Literal[...]` return type is optional but lets LangGraph draw the
# possible branches in the diagram — good habit!
def route_after_review(state: State) -> Literal["revise", "publish"]:
    if state["score"] >= PASSING_SCORE:
        return "publish"
    if state["attempts"] >= MAX_ATTEMPTS:
        # ⚠️ Always give a loop an exit! Without this guard, a bad reviewer
        #    could loop forever. (LangGraph also has a safety net:
        #    `recursion_limit`, default 25 steps — see below.)
        return "publish"
    return "revise"


# ---------------------------------------------------------------------------
# BUILD THE GRAPH
# ---------------------------------------------------------------------------
builder = StateGraph(State)
builder.add_node("draft", draft)
builder.add_node("review", review)
builder.add_node("revise", revise)
builder.add_node("publish", publish)

builder.add_edge(START, "draft")
builder.add_edge("draft", "review")
builder.add_conditional_edges("review", route_after_review)   # <- the branch
builder.add_edge("revise", "review")                          # <- the loop
builder.add_edge("publish", END)

graph = builder.compile()


if __name__ == "__main__":
    result = graph.invoke(
        {"topic": "faith and work", "history": []},
        config={"recursion_limit": 20},   # safety net against runaway loops
    )

    print("History (accumulated by the reducer):")
    for i, line in enumerate(result["history"], 1):
        print(f"  {i}. {line}")
    print(f"\nFinal draft: {result['draft']}")
    print(f"Final score: {result['score']}  |  revisions: {result['attempts']}")

    print("\nGraph diagram (Mermaid):")
    print(graph.get_graph().draw_mermaid())

# ---------------------------------------------------------------------------
# ✅ CHECK YOUR UNDERSTANDING
#   1. Change PASSING_SCORE to 10. How many revisions happen now? Why?
#   2. Remove the `attempts >= MAX_ATTEMPTS` guard and set PASSING_SCORE=99.
#      Run it — you'll hit GraphRecursionError. That's the safety net!
#   3. Remove `operator.add` from `history`. What does the history look like?
# ---------------------------------------------------------------------------
