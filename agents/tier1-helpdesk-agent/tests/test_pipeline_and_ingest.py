from pathlib import Path

from app.schemas.ticket import LLMTriageOutput
from app.services import llm_client
from ingest.sync_docs import DocMeta, chunk_document, load_chunks, parse_document

SAMPLES = Path(__file__).resolve().parents[1] / "ingest" / "sample_sops"


def test_cost_estimate_includes_cache_pricing():
    class U:
        input_tokens = 1_000_000
        output_tokens = 1_000_000
        cache_read_input_tokens = 1_000_000
        cache_creation_input_tokens = 0

    # Haiku 5.5: $0.10 in + $0.50 out + $0.01 cache read
    assert abs(llm_client.cost_from_usage("claude-haiku-5-5", U()) - 0.61) < 1e-9


def test_llm_output_schema_is_strict():
    import anthropic

    schema = anthropic.transform_schema(LLMTriageOutput)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(LLMTriageOutput.model_fields)


def test_sample_sops_parse_with_front_matter_ids():
    meta, body = parse_document(SAMPLES / "printer-offline.md")
    assert meta.doc_id == "SOP-HW-001"
    assert meta.title == "Office printer shows offline"
    assert meta.os_platform == "windows" and meta.system_name == "Windows printing"
    assert meta.last_verified_date == "2026-11-01"
    assert "doc_id" not in body
    ids = {c.doc_id for c in load_chunks(SAMPLES)}
    assert ids == {"SOP-AUTH-001", "SOP-NET-001", "SOP-HW-001"}


def test_long_sections_are_split_with_overlap():
    body = "# Big\n" + ("word " * 1000)
    chunks = chunk_document(DocMeta("SOP-X", "Big"), body)
    assert len(chunks) > 1
    assert all(len(c.content) <= 1800 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
