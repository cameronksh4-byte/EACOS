"""Chunk, embed and sync a client's SOPs into Supabase pgvector.

    python -m ingest.sync_docs --tenant acme-corp --docs ./sops [--prune] [--dry-run]

* Reads .md and .txt files. Each file is one SOP. YAML-style front matter may set
  `doc_id`, `title`, `system_name`, `os_platform` (any|windows|macos|linux|ios|
  android|chromeos) and `last_verified_date` (YYYY-MM-DD). Without `doc_id`, the
  file name is used (`vpn-connect.md` -> `VPN-CONNECT`). That is the ID the agent cites.
* Splits on headings and caps chunk size, with a little overlap.
* Only re-embeds chunks whose content changed (by SHA-256).
* `--prune` deletes chunks for SOPs (or chunks) that no longer exist locally.

Needs SUPABASE_URL, SUPABASE_SERVICE_KEY and VOYAGE_API_KEY.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import httpx

from app.config import get_settings
from app.schemas.ticket import DOC_ID_PATTERN, SOPPlatform
from app.services.chunking import _HEADING, Chunk, DocMeta, chunk_document

__all__ = ["Chunk", "DocMeta", "chunk_document", "load_chunks", "parse_document", "main"]

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
EMBED_BATCH = 64

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_document(path: Path) -> tuple[DocMeta, str]:
    """Return (metadata, body) for one SOP file."""
    text = path.read_text(encoding="utf-8")
    fields: dict[str, str] = {}
    match = _FRONT_MATTER.match(text)
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if value.strip():
                fields[key.strip().lower()] = value.strip().strip("'\"")
        text = text[match.end():]

    doc_id = fields.get("doc_id") or fields.get("sop_id")
    if not doc_id:
        doc_id = re.sub(r"[^A-Za-z0-9]+", "-", path.stem).strip("-").upper()
    if not re.match(DOC_ID_PATTERN, doc_id):
        raise SystemExit(f"{path}: invalid doc_id {doc_id!r}")

    title = fields.get("title")
    if not title:
        heading = _HEADING.search(text)
        title = heading.group(2).strip() if heading else path.stem.replace("-", " ").title()

    os_platform = fields.get("os_platform", SOPPlatform.ANY.value).lower()
    try:
        SOPPlatform(os_platform)
    except ValueError:
        raise SystemExit(f"{path}: os_platform must be one of {[p.value for p in SOPPlatform]}") from None

    verified = fields.get("last_verified_date")
    if verified:
        try:
            date.fromisoformat(verified)
        except ValueError:
            raise SystemExit(f"{path}: last_verified_date must be YYYY-MM-DD") from None

    meta = DocMeta(doc_id, title, fields.get("system_name"), os_platform, verified)
    return meta, text.strip()


def load_chunks(docs_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: dict[str, Path] = {}
    for path in sorted(p for p in docs_dir.rglob("*") if p.suffix.lower() in {".md", ".txt"}):
        meta, body = parse_document(path)
        if meta.doc_id in seen:
            raise SystemExit(f"duplicate doc_id {meta.doc_id!r} in {path} and {seen[meta.doc_id]}")
        seen[meta.doc_id] = path
        chunks.extend(chunk_document(meta, body))
    return chunks


def embed(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        resp = client.post(
            VOYAGE_URL,
            headers={"Authorization": f"Bearer {settings.voyage_api_key.get_secret_value()}"},
            json={
                "input": texts[i : i + EMBED_BATCH],
                "model": settings.embedding_model,
                "input_type": "document",
                "output_dimension": settings.embedding_dimensions,
            },
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        vectors.extend(d["embedding"] for d in data)
    return vectors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", required=True, help="tenant_id, e.g. acme-corp")
    parser.add_argument("--docs", required=True, type=Path, help="directory of SOP .md/.txt files")
    parser.add_argument("--prune", action="store_true", help="delete chunks no longer present locally")
    parser.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    args = parser.parse_args(argv)

    if not args.docs.is_dir():
        parser.error(f"{args.docs} is not a directory")

    chunks = load_chunks(args.docs)
    print(f"loaded {len(chunks)} chunks from {len({c.doc_id for c in chunks})} SOPs")

    settings = get_settings()
    key = settings.supabase_service_key.get_secret_value()
    rest = f"{settings.supabase_url.rstrip('/')}/rest/v1"
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            f"{rest}/sop_chunks",
            headers=headers,
            params={"select": "id,content_hash", "tenant_id": f"eq.{args.tenant}"},
        )
        resp.raise_for_status()
        existing = {row["id"]: row["content_hash"] for row in resp.json()}

        changed = [c for c in chunks if existing.get(c.row_id(args.tenant)) != c.content_hash]
        stale = sorted(set(existing) - {c.row_id(args.tenant) for c in chunks})
        print(f"{len(changed)} new/changed, {len(chunks) - len(changed)} unchanged, {len(stale)} stale")

        if args.dry_run:
            for c in changed:
                print(f"  upsert {c.row_id(args.tenant)}")
            for row_id in stale:
                print(f"  {'delete' if args.prune else 'keep (no --prune)'} {row_id}")
            return 0

        if changed:
            vectors = embed(client, [f"{c.meta.title}\n\n{c.content}" for c in changed])
            rows = [
                {
                    "id": c.row_id(args.tenant),
                    "tenant_id": args.tenant,
                    "doc_id": c.doc_id,
                    "title": c.meta.title,
                    "system_name": c.meta.system_name,
                    "os_platform": c.meta.os_platform,
                    "last_verified_date": c.meta.last_verified_date,
                    "source": "ingest",
                    "chunk_index": c.index,
                    "content": c.content,
                    "content_hash": c.content_hash,
                    "embedding": v,
                }
                for c, v in zip(changed, vectors, strict=True)
            ]
            resp = client.post(
                f"{rest}/sop_chunks",
                headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json=rows,
            )
            resp.raise_for_status()
            print(f"upserted {len(rows)} chunks")

        if args.prune and stale:
            for i in range(0, len(stale), 100):
                batch = stale[i : i + 100]
                ids = ",".join(f'"{s}"' for s in batch)
                resp = client.delete(
                    f"{rest}/sop_chunks",
                    headers=headers,
                    params={"tenant_id": f"eq.{args.tenant}", "id": f"in.({ids})"},
                )
                resp.raise_for_status()
            print(f"deleted {len(stale)} stale chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
