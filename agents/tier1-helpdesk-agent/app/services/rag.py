"""Tenant-isolated SOP retrieval from Supabase pgvector.

Queries are embedded with Voyage AI and matched with the `match_sop_chunks`
SQL function (see supabase/migrations). That function filters by tenant_id
*inside* the database, so one client's SOPs can never be returned for another.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import BaseModel

from app.config import get_settings
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


async def embed_query(text: str) -> list[float]:
    settings = get_settings()
    resp = await _voyage_client().post(
        VOYAGE_EMBEDDINGS_URL,
        headers={"Authorization": f"Bearer {settings.voyage_api_key.get_secret_value()}"},
        json={
            "input": [text],
            "model": settings.embedding_model,
            "input_type": "query",
            "output_dimension": settings.embedding_dimensions,
        },
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


async def retrieve_relevant_sops(
    tenant_id: str, query: str, top_k: int | None = None, os_platform: str | None = None
) -> list[SopChunk]:
    """Return the closest SOP chunks for this tenant, best first.

    Raises on network/database errors; the caller decides how to degrade.
    """
    settings = get_settings()
    embedding = await embed_query(query[: settings.max_body_chars])
    rows = await supabase_client.rpc(
        "match_sop_chunks",
        {
            "p_tenant_id": tenant_id,
            "p_query_embedding": embedding,
            "p_match_count": top_k or settings.rag_top_k,
            "p_min_similarity": settings.rag_min_similarity,
            "p_os_platform": os_platform,
        },
    )
    chunks = [SopChunk.model_validate(row) for row in rows]
    logger.info("rag tenant=%s matched=%d", tenant_id, len(chunks))
    return chunks


async def aclose() -> None:
    global _voyage
    if _voyage is not None:
        await _voyage.aclose()
        _voyage = None
