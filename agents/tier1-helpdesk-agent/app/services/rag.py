"""Tenant-isolated SOP retrieval from Supabase pgvector.

The ticket (plus any screenshot findings) is embedded once with Voyage AI. The
same vector feeds outage detection and SOP search. `match_sop_chunks` filters
by tenant_id, and optionally by os_platform, inside the database, so one
client's SOPs can never come back for another.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import BaseModel

from app.config import get_settings
from app.schemas.ticket import IncomingTicketWebhook, VisionContext
from app.services import supabase_client

logger = logging.getLogger(__name__)

VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"

_voyage: httpx.AsyncClient | None = None


class SopChunk(BaseModel):
    doc_id: str
    title: str
    system_name: str | None = None
    os_platform: str = "any"
    content: str
    similarity: float


def _voyage_client() -> httpx.AsyncClient:
    global _voyage
    if _voyage is None:
        _voyage = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
    return _voyage


def build_query_text(payload: IncomingTicketWebhook, vision: VisionContext | None) -> str:
    """Subject + body + screenshot findings (error codes matter most for retrieval)."""
    settings = get_settings()
    parts = [payload.subject[: settings.max_subject_chars], payload.body[: settings.max_body_chars]]
    if vision and vision.extractions:
        for e in vision.extractions:
            parts.append(" ".join(filter(None, [e.application_name, " ".join(e.error_codes), e.dialog_text])))
    return "\n".join(p for p in parts if p).strip()


async def embed_query(text: str) -> list[float]:
    settings = get_settings()
    resp = await _voyage_client().post(
        VOYAGE_EMBEDDINGS_URL,
        headers={"Authorization": f"Bearer {settings.voyage_api_key.get_secret_value()}"},
        json={
            "input": [text[: settings.max_body_chars + 2000]],
            "model": settings.embedding_model,
            "input_type": "query",
            "output_dimension": settings.embedding_dimensions,
        },
    )
    resp.raise_for_status()
    vector = resp.json()["data"][0]["embedding"]
    if len(vector) != settings.embedding_dimensions:
        raise ValueError(f"embedding has {len(vector)} dims, expected {settings.embedding_dimensions}")
    return vector


async def embed_documents(texts: list[str], batch: int = 64) -> list[list[float]]:
    """Embed SOP text for storage (input_type=document)."""
    settings = get_settings()
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch):
        resp = await _voyage_client().post(
            VOYAGE_EMBEDDINGS_URL,
            headers={"Authorization": f"Bearer {settings.voyage_api_key.get_secret_value()}"},
            json={"input": texts[i : i + batch], "model": settings.embedding_model,
                  "input_type": "document", "output_dimension": settings.embedding_dimensions},
        )
        resp.raise_for_status()
        vectors.extend(d["embedding"] for d in sorted(resp.json()["data"], key=lambda d: d["index"]))
    return vectors


async def retrieve_relevant_sops(
    tenant_id: str,
    query: str = "",
    top_k: int | None = None,
    os_platform: str | None = None,
    embedding: list[float] | None = None,
    min_similarity: float | None = None,
) -> list[SopChunk]:
    """Closest SOP chunks for this tenant, best first. Raises on I/O errors."""
    settings = get_settings()
    vector = embedding if embedding is not None else await embed_query(query)
    rows = await supabase_client.rpc(
        "match_sop_chunks",
        {
            "p_tenant_id": tenant_id,
            "p_query_embedding": vector,
            "p_match_count": top_k or settings.rag_top_k,
            "p_min_similarity": settings.rag_min_similarity if min_similarity is None else min_similarity,
            "p_os_platform": os_platform,
        },
    )
    chunks = [SopChunk.model_validate(row) for row in rows]
    logger.info("rag tenant=%s os=%s matched=%d", tenant_id, os_platform or "any", len(chunks))
    return chunks


async def aclose() -> None:
    global _voyage
    if _voyage is not None:
        await _voyage.aclose()
        _voyage = None
