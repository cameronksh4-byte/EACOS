"""
LESSON 4 — Putting it together: a tool-calling AI agent
========================================================

Run it:   python 04_tool_calling_agent.py

          Works offline with a scripted "fake" model by default.
          To use a real LLM (Claude):
              pip install langchain-anthropic
              export ANTHROPIC_API_KEY=sk-ant-...
              python 04_tool_calling_agent.py

THE BIG IDEA
------------
An "agent" is just Lesson 2's loop, where the ROUTER is decided by an LLM:

   START ► agent ──► did the LLM ask to use a tool? ──yes──► tools ─┐
             ▲                    │                                 │
             │                    no                                │
             │                    ▼                                 │
             │                   END                                │
             └──────────────────────────────────────────────────────┘

  1. The `agent` node sends the conversation to the LLM.
  2. The LLM either answers, OR replies with a "tool call"
     (e.g. "please run get_verse(reference='John 3:16')").
  3. If it's a tool call, the `tools` node runs the Python function and
     appends the result to the conversation, then loops back to the LLM.
  4. Repeat until the LLM gives a final answer.

New concepts:
  * MessagesState  — a ready-made State with a `messages` list + reducer
  * @tool          — turns a Python function into something an LLM can call
  * ToolNode / tools_condition — prebuilt node + router for the loop above
"""

import os

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition


# ---------------------------------------------------------------------------
# STEP 1: TOOLS
# ---------------------------------------------------------------------------
# The docstring and type hints are NOT decoration — they are sent to the LLM
# so it knows what the tool does and how to call it. Write them carefully!
VERSES = {
    "john 3:16": "For God so loved the world, that he gave his only begotten "
                 "Son, that whosoever believeth in him should not perish, "
                 "but have everlasting life.",
    "psalm 23:1": "The LORD is my shepherd; I shall not want.",
    "philippians 4:13": "I can do all things through Christ which "
                        "strengtheneth me.",
}


@tool
def get_verse(reference: str) -> str:
    """Look up the text of a Bible verse (KJV) by reference, e.g. 'John 3:16'."""
    return VERSES.get(reference.strip().lower(), f"Verse '{reference}' not found.")


@tool
def word_count(text: str) -> int:
    """Count the number of words in a piece of text."""
    return len(text.split())


TOOLS = [get_verse, word_count]


# ---------------------------------------------------------------------------
# STEP 2: THE MODEL
# ---------------------------------------------------------------------------
# `bind_tools` tells the LLM which tools exist. The model then decides
# on its own whether (and which) to call.
def make_model():
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic

        print("(using Claude)\n")
        return ChatAnthropic(model="claude-sonnet-5-5").bind_tools(TOOLS)

    print("(no ANTHROPIC_API_KEY found — using a scripted fake model)\n")
    return FakeToolCallingModel()


class FakeToolCallingModel:
    """Imitates an LLM so the lesson runs offline. It replays a fixed script:
    call get_verse -> call word_count -> give a final answer.
    💡 Faking the model like this is also a great way to UNIT TEST graphs."""

    def invoke(self, messages):
        tool_results = [m for m in messages if m.type == "tool"]
        step = len(tool_results)
        if step == 0:
            return AIMessage("", tool_calls=[{
                "name": "get_verse", "args": {"reference": "John 3:16"}, "id": "call_1"}])
        if step == 1:
            return AIMessage("", tool_calls=[{
                "name": "word_count", "args": {"text": tool_results[0].content}, "id": "call_2"}])
        return AIMessage(
            f'John 3:16 reads: "{tool_results[0].content}" '
            f"It has {tool_results[1].content} words."
        )


model = make_model()


# ---------------------------------------------------------------------------
# STEP 3: THE AGENT NODE
# ---------------------------------------------------------------------------
# MessagesState already has:  messages: Annotated[list, add_messages]
# so returning {"messages": [reply]} APPENDS the reply (Lesson 2's reducer!).
def agent(state: MessagesState) -> dict:
    reply = model.invoke(state["messages"])
    return {"messages": [reply]}


# ---------------------------------------------------------------------------
# STEP 4: THE GRAPH
# ---------------------------------------------------------------------------
builder = StateGraph(MessagesState)
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode(TOOLS))     # runs whatever tools were requested

builder.add_edge(START, "agent")
# tools_condition is a prebuilt router: it returns "tools" if the last
# message has tool calls, otherwise END. (Compare with Lesson 2's router.)
builder.add_conditional_edges("agent", tools_condition)
builder.add_edge("tools", "agent")             # loop back so the LLM sees results

graph = builder.compile(checkpointer=InMemorySaver())   # memory from Lesson 3


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "bible-study-1"}}
    question = "Look up John 3:16 and tell me how many words it has."
    print(f"USER: {question}\n")

    # stream_mode="values" yields the full state after each step, so we can
    # watch the conversation grow one message at a time.
    for state in graph.stream(
        {"messages": [HumanMessage(question)]}, config, stream_mode="values"
    ):
        last = state["messages"][-1]
        if last.type == "ai" and last.tool_calls:
            for call in last.tool_calls:
                print(f"🤖 AGENT wants to call: {call['name']}({call['args']})")
        elif last.type == "tool":
            text = str(last.content)
            short = text if len(text) <= 60 else text[:60] + "..."
            print(f"🔧 TOOL {last.name} returned: {short}")
        elif last.type == "ai":
            print(f"\n🤖 AGENT final answer:\n{last.content}")

# ---------------------------------------------------------------------------
# ✅ CHECK YOUR UNDERSTANDING
#   1. Add a new tool `get_reading_plan(days: int)` and ask a question that
#      needs it (requires the real model — the fake one follows a script).
#   2. With the real model, send a follow-up on the SAME thread_id:
#      "Now do Psalm 23:1." Notice it remembers the conversation.
#   3. Combine with Lesson 3: add interrupt() inside a tool so a human must
#      approve before it runs.
# ---------------------------------------------------------------------------
