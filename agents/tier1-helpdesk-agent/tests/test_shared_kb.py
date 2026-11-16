"""The shared baseline knowledge base (knowledge_base/) and its escalate-only SOPs."""

from pathlib import Path

import pytest

from app.config import get_settings
from app.schemas.ticket import (
    AgentTriageResult,
    IncomingTicketWebhook,
    LLMTriageOutput,
    ScopeVerdict,
    SeverityLevel,
    TicketCategory,
    TriageAction,
    is_escalate_only,
)
from app.services import guardrails, llm_router, pipeline, rag
from app.services.chunking import MAX_CHUNK_CHARS
from ingest import build_shared_kb
from ingest.sync_docs import load_chunks
from tests.fakes import triage_output

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "knowledge_base" / "sops"


def ticket(**kw) -> IncomingTicketWebhook:
    base = dict(tenant_id="acme-corp", ticket_id="T-9", reporter_email="pat@acme.com",
                subject="Files renamed", body="All my files have a strange extension and there's a note",
                source_system="api")
    base.update(kw)
    return IncomingTicketWebhook(**base)


# ---------------------------------------------------------------------------
# The corpus itself
# ---------------------------------------------------------------------------

def test_generated_sops_match_the_source_page():
    assert build_shared_kb.main(["--check"]) == 0


def test_every_kb_sop_loads_as_one_valid_chunk():
    chunks = load_chunks(KB)
    ids = [c.doc_id for c in chunks]
    assert len(ids) == len(set(ids)), "each SOP must be a single chunk"
    assert all(len(c.content) <= MAX_CHUNK_CHARS for c in chunks)
    assert all(c.meta.last_verified_date for c in chunks)

    cases = [i for i in ids if i.startswith("KB-") and not i.startswith(("KB-ESC-TRIGGER-", "KB-POLICY-"))]
    triggers = [i for i in ids if i.startswith("KB-ESC-TRIGGER-")]
    assert len(triggers) == 15
    assert {"KB-POLICY-GUARDRAILS", "KB-POLICY-HANDOFF", "KB-ESC-ONBOARD-NEW-STARTER",
            "KB-ESC-OFFBOARD-LEAVER"} <= set(ids)
    # 30 library cases (9 marked "Escalate to human") plus the hand-written ones.
    generated = [c for c in cases if (KB / "cases" / f"{c.lower()}.md").exists()]
    assert len(generated) == 30
    assert sum(is_escalate_only(c) for c in generated) == 9


def test_kb_never_tells_the_agent_to_collect_secrets():
    text = "\n".join(p.read_text(encoding="utf-8").lower() for p in KB.rglob("*.md"))
    for phrase in ("send me your password", "tell me your password", "share your mfa code",
                   "what is your password", "reply with your password"):
        assert phrase not in text


def test_security_and_phi_cases_are_escalate_only():
    phi = (KB / "escalation" / "kb-esc-trigger-10.md").read_text(encoding="utf-8")
    assert "PHI" in phi and "doc_id: KB-ESC-TRIGGER-10" in phi
    for name in ("kb-esc-sec-01.md", "kb-esc-sec-02.md", "kb-esc-sec-03.md"):
        assert (KB / "cases" / name).exists()


# ---------------------------------------------------------------------------
# The literal reader treats the page as data only
# ---------------------------------------------------------------------------

def test_literal_reader_handles_quoting_and_escapes():
    src = 'x; var CASES=[{cat:"A",\'title\':\'It\\\'s \\u00e9\',"steps":["a, b: c","d"],n:3,ok:true}];'
    assert build_shared_kb.read_array(src, "CASES") == [
        {"cat": "A", "title": "It's \u00e9", "steps": ["a, b: c", "d"], "n": 3, "ok": True}]


@pytest.mark.parametrize("src", [
    "var CASES=[alert(1)];",
    "var CASES=[{a:fetch('x')}];",
    "var CASES=[{a:1+2}];",
    "var CASES=[{a:`template`}];",
    "var CASES=[{a:\"unterminated}];",
    "var OTHER=[];",
])
def test_literal_reader_rejects_anything_but_plain_data(src):
    with pytest.raises(build_shared_kb.LiteralError):
        build_shared_kb.read_array(src, "CASES")


def test_markup_in_source_text_is_stripped_and_headings_escaped():
    docs = build_shared_kb.case_docs([{
        "cat": "VPN", "title": "<b>Bold</b> title", "v": "mixed", "sym": "# not a heading",
        "steps": ["<script>x</script>step"], "fail": "f", "esc": "e"}])
    text = docs[0].text
    assert "<b>" not in text and "<script>" not in text
    assert "\\# not a heading" in text
    assert docs[0].doc_id == "KB-VPN-01"


# ---------------------------------------------------------------------------
# Retrieval and prompt
# ---------------------------------------------------------------------------

async def test_retrieval_includes_the_shared_tenant(env):
    env["sb"].sops = [{"doc_id": "KB-VPN-01", "title": "VPN", "os_platform": "any", "content": "x",
                       "similarity": 0.8, "is_shared": True}]
    chunks = await rag.retrieve_relevant_sops("acme-corp", embedding=[0.1] * 1024)
    params = [p for fn, p in env["sb"].rpc_calls if fn == "match_sop_chunks"][-1]
    assert params["p_tenant_id"] == "acme-corp"
    assert params["p_shared_tenant_id"] == "shared-kb"
    assert chunks[0].shared is True


async def test_shared_kb_can_be_switched_off(env, monkeypatch):
    monkeypatch.setenv("SHARED_KB_TENANT_ID", "")
    get_settings.cache_clear()
    await rag.retrieve_relevant_sops("acme-corp", embedding=[0.1] * 1024)
    params = [p for fn, p in env["sb"].rpc_calls if fn == "match_sop_chunks"][-1]
    assert params["p_shared_tenant_id"] is None


def test_prompt_labels_baseline_and_client_sops():
    sops = [rag.SopChunk(doc_id="SOP-NET-001", title="VPN", content="client steps", similarity=0.9),
            rag.SopChunk(doc_id="KB-VPN-01", title="VPN", content="generic", similarity=0.8, shared=True)]
    text = llm_router.build_user_message(ticket(), sops, get_settings())
    assert '<sop doc_id="SOP-NET-001" title="VPN" os_platform="any" source="client">' in text
    assert '<sop doc_id="KB-VPN-01" title="VPN" os_platform="any" source="baseline">' in text
    system = llm_router.system_prompt(get_settings())
    for rule in ('source="baseline"', '"KB-ESC-"', "Handoff packet", "PHI", "remote access",
                 "Never translate an error code"):
        assert rule in system


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

def test_shared_tenant_can_never_be_a_client(monkeypatch):
    settings = get_settings()
    assert not settings.is_tenant_allowed("shared-kb")
    assert not any("SHARED_KB" in m for m in settings.missing_core_settings())

    monkeypatch.setenv("ALLOWED_TENANT_IDS", "acme-corp,shared-kb")
    get_settings.cache_clear()
    settings = get_settings()
    assert not settings.is_tenant_allowed("shared-kb")
    assert any("SHARED_KB_TENANT_ID" in m for m in settings.missing_core_settings())


def test_shared_tenant_can_never_be_the_website_tenant(monkeypatch):
    monkeypatch.setenv("WEBCHAT_TENANT_ID", "shared-kb")
    get_settings.cache_clear()
    assert any("SHARED_KB_TENANT_ID" in m for m in get_settings().missing_core_settings())


# ---------------------------------------------------------------------------
# Escalate-only guardrail
# ---------------------------------------------------------------------------

def _result(**kw) -> AgentTriageResult:
    base = dict(ticket_id="T-9", category=TicketCategory.HARDWARE_PRINTER, severity=SeverityLevel.HIGH,
                confidence_score=0.95, requires_tier2_escalation=False, cited_sop_ids=["KB-ESC-TRIGGER-01"],
                draft_user_reply="Disconnect from the network.")
    base.update(kw)
    return AgentTriageResult(**base)


def test_citing_an_escalate_only_sop_never_resolves():
    r = _result()
    assert r.action is TriageAction.ESCALATE_TIER_2 and r.requires_tier2_escalation
    assert "escalate_only_sop" in r.guardrail_overrides
    assert not r.deflected


def test_escalate_only_clarification_keeps_question_but_alerts_a_person():
    r = _result(action=TriageAction.ASK_CLARIFICATION, thread_id="slack:C1:1.1", clarifying_question="Which device?",
                clarifications_used=1)
    assert r.action is TriageAction.ASK_CLARIFICATION
    assert r.requires_tier2_escalation
    assert "escalate_only_sop" in r.guardrail_overrides


def test_ordinary_baseline_sop_can_resolve():
    r = _result(cited_sop_ids=["KB-VPN-01"])
    assert r.action is TriageAction.RESOLVE and not r.requires_tier2_escalation


def _draft(**kw) -> LLMTriageOutput:
    base = dict(scope=ScopeVerdict.IN_SCOPE, action=TriageAction.RESOLVE, category=TicketCategory.HARDWARE_PRINTER,
                severity=SeverityLevel.HIGH, confidence_score=0.95, requires_tier2_escalation=False,
                cited_sop_ids=["KB-ESC-TRIGGER-04"], draft_user_reply="1. Unplug it and stop using it.",
                clarifying_question="", requested_item="", missing_sop_topic="", known_facts=[],
                internal_tech_notes="device: unknown")
    base.update(kw)
    return LLMTriageOutput(**base)


def test_grounded_first_aid_is_sent_with_the_handoff():
    result = guardrails.enforce_policy(ticket(), _draft(), {"KB-ESC-TRIGGER-04"}, get_settings(), "m")
    assert result.action is TriageAction.ESCALATE_TIER_2
    assert result.draft_user_reply.startswith("1. Unplug it and stop using it.")
    assert "reference number is T-9" in result.draft_user_reply
    assert "Unsent AI draft" not in (result.internal_tech_notes or "")


def test_ungrounded_escalate_only_draft_is_held_back():
    result = guardrails.enforce_policy(ticket(), _draft(confidence_score=0.6), {"KB-ESC-TRIGGER-04"},
                                       get_settings(), "m")
    assert result.draft_user_reply == guardrails.handoff_message(get_settings(), "T-9")
    assert "Unsent AI draft" in result.internal_tech_notes


def test_escalate_only_question_tells_the_user_a_person_is_coming():
    d = _draft(action=TriageAction.ASK_CLARIFICATION, clarifying_question="Which device is it?")
    t = ticket(thread_id="slack:C1:1.1")
    result = guardrails.enforce_policy(t, d, {"KB-ESC-TRIGGER-04"}, get_settings(), "m", clarifications_used=1)
    assert result.action is TriageAction.ASK_CLARIFICATION and result.requires_tier2_escalation
    assert "passed it on" in result.draft_user_reply and "Which device is it?" in result.draft_user_reply


async def test_pipeline_escalates_a_hardware_failure_with_first_aid(env):
    env["sb"].sops = [{"doc_id": "KB-ESC-TRIGGER-04", "title": "Must escalate: Hardware failure",
                       "os_platform": "any", "content": "Unplug if there's a smell.", "similarity": 0.83,
                       "is_shared": True}]
    env["claude"].triage = [triage_output(
        category="hardware_printer", severity="P2_HIGH", cited_sop_ids=["KB-ESC-TRIGGER-04"],
        draft_user_reply="1. Unplug the laptop and move it away.\n2. Don't turn it back on.")]
    result = await pipeline.process_ticket(ticket(subject="Laptop smells of burning", body="It got hot and shut off"))
    assert result.requires_tier2_escalation and result.action is TriageAction.ESCALATE_TIER_2
    assert "Unplug the laptop" in result.draft_user_reply
    assert result.cited_sop_ids == ["KB-ESC-TRIGGER-04"]
    assert 'source="baseline"' in env["claude"].last_triage_text()
