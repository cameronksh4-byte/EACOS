# LangGraph tutorial: learn by running

Four short, runnable lessons. Each one adds a single new idea to the previous one.
Every file has teaching notes in its comments and ends with **"Check your understanding"** exercises.

| # | File | You'll learn |
|---|------|--------------|
| 1 | `01_basics.py` | **State, Nodes, Edges**: the three building blocks. `invoke`, `stream`, drawing the graph |
| 2 | `02_branching_and_loops.py` | **Conditional edges**, **loops**, **reducers** (accumulating state), `recursion_limit` |
| 3 | `03_memory_and_human_in_the_loop.py` | **Checkpointers** and `thread_id`, **`interrupt()`** to pause for human approval, resuming with `Command`, time travel |
| 4 | `04_tool_calling_agent.py` | A real **tool-calling agent**: `MessagesState`, `@tool`, `ToolNode`, `tools_condition` |

## Setup

```bash
cd tutorials/langgraph
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python 01_basics.py
python 02_branching_and_loops.py
python 03_memory_and_human_in_the_loop.py
python 04_tool_calling_agent.py
```

**No API key needed.** Lessons 1–3 have no LLM in them. Lesson 4 uses a scripted fake model unless
`ANTHROPIC_API_KEY` is set (then run `pip install langchain-anthropic` and it uses Claude).

## The mental model

```
            ┌───────────────── STATE (a shared dict) ─────────────────┐
            │                                                         │
 START ──► node A ──► node B ──► router() ──► node C ──► END
             │          │           │
        reads state  returns     returns the NAME
        returns a    an update   of the next node
        partial      (merged in
        update       via reducers)
```

* **Node** = a function `state -> partial update`.
* **Edge** = "then run this". **Conditional edge** = "run whichever node this function names".
* **Reducer** = how an update is merged into the state (overwrite by default; `operator.add` / `add_messages` to append).
* **Checkpointer** = saves the state after every step, per `thread_id`. This is what makes memory, pausing, and resuming work.
* **Agent** = a loop where an LLM is the router: *call a tool, or finish?*

## Suggested study path

1. Run each lesson, then **read it top to bottom**. The comments are the lesson.
2. Do the exercises at the bottom of each file. Breaking things on purpose teaches the most.
3. Paste the printed Mermaid diagrams into <https://mermaid.live> to see your graphs.
4. Next steps: subgraphs, parallel branches (fan-out/fan-in with `Send`), `stream_mode="messages"` for token streaming,
   and a persistent checkpointer (`langgraph-checkpoint-sqlite` / `-postgres`).
