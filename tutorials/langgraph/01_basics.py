"""
LESSON 1 — The three building blocks: State, Nodes, Edges
==========================================================

Run it:   python 01_basics.py

THE BIG IDEA
------------
LangGraph lets you describe a program as a *graph*:

    START ──► greet ──► shout ──► END

  * STATE  — a shared dictionary that flows through the graph.
  * NODES  — plain Python functions. Each one READS the state and
             RETURNS a dict of the fields it wants to UPDATE.
  * EDGES  — arrows that say "after this node, run that node".

That's it. Everything else in LangGraph (agents, memory, tools,
human approval) is built from these three pieces.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


# ---------------------------------------------------------------------------
# STEP 1: Define the STATE
# ---------------------------------------------------------------------------
# The state is a TypedDict — a "schema" for the data passed between nodes.
# Think of it as the whiteboard every node can read from and write to.
class State(TypedDict):
    name: str
    message: str


# ---------------------------------------------------------------------------
# STEP 2: Write the NODES
# ---------------------------------------------------------------------------
# A node receives the *whole* current state, and returns ONLY the keys it
# changed. LangGraph merges that partial update into the state for you.
#
# 💡 TEACHING NOTE: You never mutate `state` directly. Return a new dict.
#    This makes every step easy to trace, test, and replay.
def greet(state: State) -> dict:
    print(f"  [greet] received state: {state}")
    return {"message": f"Hello, {state['name']}!"}


def shout(state: State) -> dict:
    print(f"  [shout] received state: {state}")
    return {"message": state["message"].upper()}


# ---------------------------------------------------------------------------
# STEP 3: Wire it together with EDGES
# ---------------------------------------------------------------------------
builder = StateGraph(State)          # 1. a builder that knows our State shape

builder.add_node("greet", greet)     # 2. register nodes by name
builder.add_node("shout", shout)

builder.add_edge(START, "greet")     # 3. START is the special entry point
builder.add_edge("greet", "shout")
builder.add_edge("shout", END)       #    END is the special exit point

# STEP 4: COMPILE — this validates the graph (no dangling nodes, etc.)
# and turns it into a runnable object.
graph = builder.compile()


if __name__ == "__main__":
    print("Running the graph...\n")

    # .invoke() takes the initial state and returns the final state.
    result = graph.invoke({"name": "Cameron", "message": ""})
    print(f"\nFinal state: {result}")

    # 💡 BONUS: .stream() shows you each node's update as it happens.
    #    This is the #1 debugging tool in LangGraph.
    print("\nStreaming each step:")
    for step in graph.stream({"name": "Grace", "message": ""}):
        print(f"  {step}")

    # 💡 BONUS: print the graph as a diagram (Mermaid syntax — paste it
    #    into https://mermaid.live to see a picture).
    print("\nGraph diagram (Mermaid):")
    print(graph.get_graph().draw_mermaid())

# ---------------------------------------------------------------------------
# ✅ CHECK YOUR UNDERSTANDING
#   1. Add a third node `add_emoji` that appends " 👋" to the message, and
#      put it between `shout` and END.
#   2. What happens if `greet` returns {"name": "Someone else"}? Try it.
#   3. Remove `builder.add_edge("shout", END)` — what error do you get?
# ---------------------------------------------------------------------------
