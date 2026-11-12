from pathlib import Path

from app.schemas.ticket import IncomingTicketWebhook, LLMTriageOutput, ScopeVerdict
from app.services import llm_router, pipeline, rag, usage
from ingest.sync_docs import chunk_document, load_chunks, parse_document

TICKET = IncomingTicketWebhook(
    tenant_id="acme-corp", ticket_id="T-5", reporter_email="a@acme.com", subject="s", body="b"
)
SAMPLES = Path(__file__).resolve().parents[1] / "ingest" / "sample_sops"


async def test_budget_cap_skips_the_model(monkeypatch):
    async def exhausted(_):
        return True

    async def must_not_run(*_):
        raise AssertionError("model called while over budget")

    monkeypatch.setattr(usage, "budget_exhausted", exhausted)
    monkeypatch.setattr(llm_router, "route_ticket", must_not_run)
    result = await pipeline.process_ticket(TICKET)
    assert result.requires_tier2_escalation is True
    assert "budget" in (result.internal_tech_notes or "")


async def test_rag_outage_still_declines_out_of_scope(monkeypatch):
    async def ok(_):
        return False

    async def rag_down(**_):
        raise ConnectionError("supabase down")

    async def route(payload, sops):
        assert sops == []
        return llm_router.RouterDecision(output=llm_router.refusal_output(), model="claude-haiku-5-5")

    monkeypatch.setattr(usage, "budget_exhausted", ok)
    monkeypatch.setattr(rag, "retrieve_relevant_sops", rag_down)
    monkeypatch.setattr(llm_router, "route_ticket", route)
    result = await pipeline.process_ticket(TICKET)
    assert result.scope is ScopeVerdict.NOT_IT_SUPPORT


def test_cost_estimate_includes_cache_pricing():
    class U:
        input_tokens = 1_000_000
        output_tokens = 1_000_000
        cache_read_input_tokens = 1_000_000
        cache_creation_input_tokens = 0

    # Haiku 5.5: $0.10 in + $0.50 out + $0.01 cache read
    assert abs(usage.estimate_cost_usd("claude-haiku-5-5", U()) - 0.61) < 1e-9


def test_llm_output_schema_is_strict():
    import anthropic

    schema = anthropic.transform_schema(LLMTriageOutput)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(LLMTriageOutput.model_fields)


def test_sample_sops_parse_with_front_matter_ids():
    sop_id, title, body = parse_document(SAMPLES / "vpn-connect.md")
    assert sop_id == "SOP-NET-001"
    assert title == "GlobalProtect VPN will not connect"
    assert "sop_id" not in body
    ids = {c.sop_id for c in load_chunks(SAMPLES)}
    assert ids == {"SOP-AUTH-001", "SOP-NET-001", "SOP-HW-001"}


def test_long_sections_are_split_with_overlap():
    body = "# Big\n" + ("word " * 1000)
    chunks = chunk_document("SOP-X", "Big", body)
    assert len(chunks) > 1
    assert all(len(c.content) <= 1800 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
