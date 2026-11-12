"""Chunk, embed and sync a client's SOPs into Supabase pgvector.

    python -m ingest.sync_docs --tenant acme-corp --docs ./sops [--prune] [--dry-run]

* Reads .md and .txt files. Each file is one SOP. Its sop_id comes from a
  `sop_id: ...` line in front matter, or else from the file name
  (`vpn-connect.md` -> `VPN-CONNECT`). That is the ID the agent cites.
* Splits on headings and caps chunk size, with a little overlap.
* Only re-embeds chunks whose content changed (by SHA-256).
* `--prune` deletes chunks for SOPs (or chunks) that no longer exist locally.

Needs SUPABASE_URL, SUPABASE_SERVICE_KEY and VOYAGE_API_KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import get_settings

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
MAX_CHUNK_CHARS = 1800
OVERLAP_CHARS = 200
EMBED_BATCH = 64

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)


@dataclass
class Chunk:
    sop_id: str
    title: str
    index: int
    content: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.title}\n{self.content}".encode()).hexdigest()

    def row_id(self, tenant: str) -> str:
        return f"{tenant}:{self.sop_id}#{self.index}"


def parse_document(path: Path) -> tuple[str, str, str]:
    """Return (sop_id, title, body) for one SOP file."""
    text = path.read_text(encoding="utf-8")
    sop_id = None
    title = None
    match = _FRONT_MATTER.match(text)
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "sop_id" and value.strip():
                sop_id = value.strip()
            elif key.strip() == "title" and value.strip():
                title = value.strip()
        text = text[match.end():]
    if not sop_id:
        sop_id = re.sub(r"[^A-Za-z0-9]+", "-", path.stem).strip("-").upper()
    if not title:
        heading = _HEADING.search(text)
        title = heading.group(2).strip() if heading else path.stem.replace("-", " ").title()
    return sop_id, title, text.strip()


def _split_long(section: str) -> list[str]:
    if len(section) <= MAX_CHUNK_CHARS:
        return [section]
    pieces: list[str] = []
    start = 0
    while start < len(section):
        end = min(len(section), start + MAX_CHUNK_CHARS)
        if end < len(section):
            # Prefer breaking at a paragraph, then a line.
            cut = max(section.rfind("\n\n", start, end), section.rfind("\n", start, end))
            if cut > start + MAX_CHUNK_CHARS // 2:
                end = cut
        pieces.append(section[start:end].strip())
        if end >= len(section):
            break
        start = max(end - OVERLAP_CHARS, start + 1)
    return [p for p in pieces if p]


def chunk_document(sop_id: str, title: str, body: str) -> list[Chunk]:
    starts = [m.start() for m in _HEADING.finditer(body)] or [0]
    if starts[0] != 0:
        starts.insert(0, 0)
    sections = [body[a:b].strip() for a, b in zip(starts, starts[1:] + [len(body)], strict=True)]
    pieces = [p for s in sections if s for p in _split_long(s)]
    return [Chunk(sop_id, title, i, p) for i, p in enumerate(pieces)]


def load_chunks(docs_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: dict[str, Path] = {}
    for path in sorted(p for p in docs_dir.rglob("*") if p.suffix.lower() in {".md", ".txt"}):
        sop_id, title, body = parse_document(path)
        if sop_id in seen:
            raise SystemExit(f"duplicate sop_id {sop_id!r} in {path} and {seen[sop_id]}")
        seen[sop_id] = path
        chunks.extend(chunk_document(sop_id, title, body))
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
    print(f"loaded {len(chunks)} chunks from {len({c.sop_id for c in chunks})} SOPs")

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
            vectors = embed(client, [f"{c.title}\n\n{c.content}" for c in changed])
            rows = [
                {
                    "id": c.row_id(args.tenant),
                    "tenant_id": args.tenant,
                    "sop_id": c.sop_id,
                    "title": c.title,
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
