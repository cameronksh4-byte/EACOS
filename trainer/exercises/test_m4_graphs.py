"""Checkpoint tests for Module 4's exercise. Run with:  uv run python -m trainer.cli check M4"""

from __future__ import annotations

import importlib
import os
import sqlite3

import pytest

ex = importlib.import_module(os.environ.get("M4_TARGET", "trainer.exercises.m4_graphs"))
END, PAUSE, RESUME = "END", "__pause__", "__resume__"


def base(**kw):
    return {"resolution": None, "had_error": False, "repairs": 0, "max_repairs": 2, "steps": 1, "max_steps": 5} | kw


@pytest.mark.parametrize("state, expected", [
    (base(resolution={"verdict": "dispute"}), "policy"),
    (base(resolution={"verdict": "dispute"}, had_error=True), "policy"),
    (base(had_error=True), "repair"),
    (base(had_error=True, repairs=2), "human_review"),
    (base(steps=5), "human_review"),
    (base(), "agent"),
])
def test_m4_ex1_route_after_tools(state, expected):
    assert ex.route_after_tools(state) == expected


def test_m4_ex2_checkpointer_keeps_history_and_loads_newest(tmp_path):
    cp = ex.Checkpointer(str(tmp_path / "cp.db"))
    assert cp.load("t1") is None
    cp.save("t1", "agent", {"steps": 1})
    cp.save("t1", "tools", {"steps": 2})
    cp.save("t2", "agent", {"steps": 9})
    assert cp.load("t1") == ("tools", {"steps": 2})
    assert cp.conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 3
    assert ex.Checkpointer(str(tmp_path / "cp.db")).load("t2") == ("agent", {"steps": 9})  # survives "restart"


def approval_graph(calls):
    def check(state):
        calls.append("check")
        return {"large": state["amount"] > 100}

    def approve(state):
        calls.append("approve")
        if RESUME not in state:
            return {PAUSE: f"Approve ${state['amount']}?"}
        return {"approved": state[RESUME] == "yes"}

    def record(state):
        calls.append("record")
        return {"recorded": True}

    return {
        "check": (check, lambda s: "approve" if s["large"] else "record"),
        "approve": (approve, "record"),
        "record": (record, END),
    }


def test_m4_ex3_runs_to_end_without_pausing(tmp_path):
    calls = []
    out = ex.run(approval_graph(calls), "t", ex.Checkpointer(str(tmp_path / "a.db")), start="check",
                 state={"amount": 50})
    assert out["status"] == "done" and out["state"]["recorded"] and calls == ["check", "record"]


def test_m4_ex3_pauses_and_resumes_from_a_new_process(tmp_path):
    db = str(tmp_path / "b.db")
    calls = []
    paused = ex.run(approval_graph(calls), "t", ex.Checkpointer(db), start="check", state={"amount": 500})
    assert paused["status"] == "paused" and paused["question"] == "Approve $500?"
    assert ex.Checkpointer(db).load("t")[0] == "approve"  # will re-run the paused node

    done = ex.run(approval_graph(calls), "t", ex.Checkpointer(db), resume_value="yes")  # fresh checkpointer
    assert done["status"] == "done" and done["state"]["approved"] is True and done["state"]["recorded"]
    assert RESUME not in done["state"]
    assert calls == ["check", "approve", "approve", "record"]  # paused node ran twice, check ran once


def test_m4_ex3_has_a_step_budget(tmp_path):
    loop = {"a": (lambda s: {"n": s.get("n", 0) + 1}, "a")}
    with pytest.raises(RuntimeError):
        ex.run(loop, "t", ex.Checkpointer(str(tmp_path / "c.db")), start="a", state={}, max_steps=10)


def test_m4_ex4_process_once(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "e.db"))
    payments = []

    def pay():
        payments.append(1)
        return {"paid": len(payments)}

    assert ex.process_once(conn, "evt-1", pay) == ({"paid": 1}, False)
    assert ex.process_once(conn, "evt-1", pay) == ({"paid": 1}, True)   # retry: no second payment
    assert ex.process_once(conn, "evt-2", pay) == ({"paid": 2}, False)
    assert len(payments) == 2
