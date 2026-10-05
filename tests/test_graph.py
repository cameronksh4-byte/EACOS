"""Module 4 tests: LangGraph resolver, SQLite checkpoints, human review, idempotency, batching."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from auditgate import api, pipeline
from auditgate.agent.__main__ import demo_script
from auditgate.agent.graph import IdempotencyStore, ResolutionService, initial_state
from auditgate.agent.models import ModelTurn, ScriptedModel, ToolCall
from auditgate.config import Settings
from auditgate.evals.runner import load_cases
from auditgate.extraction.schemas import DocumentType
from auditgate.pipeline import AuditReport, process_batch, process_text
from auditgate.security.sanitizer import Sanitizer

CASES = {c["id"]: c for c in load_cases()}


def report_for(case_id: str) -> AuditReport:
    case = CASES[case_id]
    return process_text(case["text"], DocumentType(case["doc_type"]), settings=Settings(provider="heuristic"),
                        sanitizer=Sanitizer(spacy_model=None))


def submit(i: str, **fields) -> ModelTurn:
    return ModelTurn(tool_calls=[ToolCall(i, "submit_resolution", fields)])


@pytest.fixture
def db(tmp_path):
    return tmp_path / "state.db"


def test_m4_graph_has_repair_and_human_review_nodes(db):
    svc = ResolutionService(ScriptedModel([]), db)
    nodes = set(svc.graph.get_graph().nodes)
    assert {"agent", "tools", "repair", "nudge", "policy", "human_review", "finalize"} <= nodes


def test_m4_dispute_waits_for_human_then_queues_email_once(db):
    report = report_for("inv_line_math_error")
    svc = ResolutionService(demo_script(report), db)
    status = svc.start(report, "inv-7781")
    assert status["state"] == "waiting_for_human"
    assert status["repairs"] == 1 and status["resolution"]["disputed_amount"] == "90.00"
    assert svc.outbox.all() == []  # nothing irreversible before a human says yes

    done = svc.resume("inv-7781", {"decision": "approve", "approver": "owner"}, idempotency_key="evt-1")
    assert done["state"] == "completed" and done["outcome"] == "dispute_email_queued"
    assert len(svc.outbox.all()) == 1


def test_m4_run_survives_a_restart(db):
    report = report_for("inv_line_math_error")
    ResolutionService(demo_script(report), db).start(report, "inv-7781")
    # A brand-new process: new service, new connections, a model that must NOT be called again.
    fresh = ResolutionService(ScriptedModel([]), db)
    assert fresh.status("inv-7781")["state"] == "waiting_for_human"
    done = fresh.resume("inv-7781", {"decision": "approve", "approver": "owner"}, idempotency_key="evt-9")
    assert done["outcome"] == "dispute_email_queued"


def test_m4_duplicate_webhook_is_replayed_not_reapplied(db):
    report = report_for("inv_line_math_error")
    svc = ResolutionService(demo_script(report), db)
    svc.start(report, "inv-7781")
    first = svc.resume("inv-7781", {"decision": "approve"}, idempotency_key="evt-1")
    second = svc.resume("inv-7781", {"decision": "reject"}, idempotency_key="evt-1")  # retry of same event
    assert first["replayed"] is False and second["replayed"] is True
    assert second["outcome"] == "dispute_email_queued" and len(svc.outbox.all()) == 1
    late = svc.resume("inv-7781", {"decision": "approve"}, idempotency_key="evt-2")  # new event, already done
    assert "not waiting" in late["error"]


def test_m4_rejection_sends_nothing(db):
    report = report_for("inv_line_math_error")
    svc = ResolutionService(demo_script(report), db)
    svc.start(report, "t")
    assert svc.resume("t", {"decision": "reject"}, idempotency_key="e")["outcome"] == "reject"
    assert svc.outbox.all() == []


def test_m4_clean_approval_skips_human_but_failed_audit_cannot(db):
    review_doc = report_for("wo_emergency_no_cap")  # REVIEW status: approval allowed by policy
    model = ScriptedModel([submit("a", verdict="approve", summary="Emergency work is expected for burst pipes.",
                                  evidence=["Priority emergency, 3.5 hours estimated"])])
    assert ResolutionService(model, db).start(review_doc, "wo")["outcome"] == "auto-approved"

    failed = report_for("inv_total_inflated")
    model = ScriptedModel([submit("a", verdict="approve", summary="It is fine, please just pay it.", evidence=["x"])])
    status = ResolutionService(model, db).start(failed, "inv")
    assert status["state"] == "waiting_for_human" and status["resolution"]["verdict"] == "escalate"
    assert any("cannot be auto-approved" in v for v in status["violations"])


def test_m4_repair_limit_routes_to_human(db):
    bad = [submit(str(i), verdict="dispute", summary="short") for i in range(10)]
    status = ResolutionService(ScriptedModel(bad), db, max_repairs=2).start(report_for("inv_line_math_error"), "t")
    assert status["state"] == "waiting_for_human" and status["repairs"] == 3
    assert "repair attempts" in status["review"]["reason"] and status["resolution"] is None


def test_m4_step_budget_routes_to_human(db):
    lookups = [ModelTurn(tool_calls=[ToolCall(str(i), "lookup_vendor_history",
                                              {"vendor_name": "Apex Electrical Services"})]) for i in range(20)]
    status = ResolutionService(ScriptedModel(lookups), db, max_steps=3).start(report_for("inv_line_math_error"), "t")
    assert status["state"] == "waiting_for_human" and status["steps"] == 3
    assert "3 steps" in status["review"]["reason"]


def test_m4_chatty_model_is_nudged(db):
    model = ScriptedModel([ModelTurn(text="Hmm, let me think."),
                           submit("a", verdict="escalate", summary="Not enough data to decide this one.",
                                  evidence=["no purchase order found"])])
    status = ResolutionService(model, db).start(report_for("bid_total_mismatch"), "t")
    assert status["resolution"]["verdict"] == "escalate"
    assert "did not call a tool" in model.seen[1][-1]


# --------------------------------------------------------------------------- idempotency store


def test_m4_idempotency_in_progress_and_stale_claims(db):
    store = IdempotencyStore(db, stale_after=0.2)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return {"ok": 1}

    worker = threading.Thread(target=lambda: store.run_once("k", slow))
    worker.start()
    started.wait(5)
    assert store.run_once("k", lambda: {"ok": 2}) == ({"state": "in_progress", "key": "k"}, True)
    release.set()
    worker.join()
    assert store.run_once("k", lambda: {"ok": 3}) == ({"ok": 1}, True)

    store.conn.execute("INSERT INTO processed_events VALUES ('crashed', NULL, ?)", (time.time() - 10,))
    assert store.run_once("crashed", lambda: {"ok": "retaken"}) == ({"ok": "retaken"}, False)


def test_m4_failed_action_releases_its_claim(db):
    store = IdempotencyStore(db)
    with pytest.raises(RuntimeError):
        store.run_once("k", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert store.run_once("k", lambda: {"ok": True}) == ({"ok": True}, False)


# --------------------------------------------------------------------------- API


def test_m4_api_resolution_flow(db, monkeypatch):
    report = report_for("inv_line_math_error")
    monkeypatch.setattr(api.app.state, "resolutions", ResolutionService(demo_script(report), db), raising=False)
    monkeypatch.setenv("AUDITGATE_PROVIDER", "heuristic")
    api.get_settings.cache_clear()
    client = TestClient(api.app)
    text = CASES["inv_line_math_error"]["text"]

    started = client.post("/resolutions", json={"text": text, "thread_id": "inv-7781"}).json()
    assert started["state"] == "waiting_for_human"
    assert client.post("/resolutions", json={"text": text, "thread_id": "inv-7781"}).json()["steps"] == 4
    assert client.get("/resolutions/inv-7781").json()["review"]["question"] == "Approve this resolution?"
    assert client.get("/resolutions/nope").status_code == 404

    hook = {"event_id": "evt-1", "thread_id": "inv-7781", "decision": "approve", "approver": "owner"}
    assert client.post("/webhooks/approval", json=hook).json()["outcome"] == "dispute_email_queued"
    assert client.post("/webhooks/approval", json=hook).json()["replayed"] is True
    clean = client.post("/resolutions", json={"text": CASES["inv_clean"]["text"], "thread_id": "x"}).json()
    assert clean["state"] == "not_needed"
    api.get_settings.cache_clear()


# --------------------------------------------------------------------------- batching


def test_m4_batch_is_bounded_and_ordered(tmp_path, monkeypatch):
    active, peak, lock = 0, 0, threading.Lock()
    real = pipeline.process_file

    def tracked(path, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        try:
            return real(path, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(pipeline, "process_file", tracked)
    paths = []
    for i, case_id in enumerate(["inv_clean", "inv_total_inflated", "bid_clean", "wo_no_tasks"] * 2):
        p = tmp_path / f"{i}_{case_id}.txt"
        p.write_text(CASES[case_id]["text"])
        paths.append(p)
    reports = asyncio.run(process_batch(paths, concurrency=2, settings=Settings(provider="heuristic")))
    assert peak == 2
    assert [r.status.value for r in reports] == ["pass", "fail", "pass", "fail"] * 2
    with pytest.raises(ValueError):
        asyncio.run(process_batch(paths, concurrency=0))


@pytest.mark.parametrize("case_id", [c for c in CASES if CASES[c]["pii_values"]])
def test_m4_no_golden_pii_in_graph_state_sent_to_model(case_id):
    task = initial_state(report_for(case_id))["transcript"][0]["text"]
    assert [v for v in CASES[case_id]["pii_values"] if v in task] == []
