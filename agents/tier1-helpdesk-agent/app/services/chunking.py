"""Heading-aware SOP chunking, shared by the ingest CLI and the Tier 2 flywheel."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.schemas.ticket import SOPPlatform

MAX_CHUNK_CHARS = 1800
OVERLAP_CHARS = 200

_HEADING = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)


@dataclass
class DocMeta:
    doc_id: str
    title: str
    system_name: str | None = None
    os_platform: str = SOPPlatform.ANY.value
    last_verified_date: str | None = None


@dataclass
class Chunk:
    meta: DocMeta
    index: int
    content: str

    @property
    def doc_id(self) -> str:
        return self.meta.doc_id

    @property
    def content_hash(self) -> str:
        m = self.meta
        key = f"{m.title}\n{m.system_name}\n{m.os_platform}\n{m.last_verified_date}\n{self.content}"
        return hashlib.sha256(key.encode()).hexdigest()

    def row_id(self, tenant: str) -> str:
        return f"{tenant}:{self.doc_id}#{self.index}"


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


def chunk_document(meta: DocMeta, body: str) -> list[Chunk]:
    starts = [m.start() for m in _HEADING.finditer(body)] or [0]
    if starts[0] != 0:
        starts.insert(0, 0)
    sections = [body[a:b].strip() for a, b in zip(starts, starts[1:] + [len(body)], strict=True)]
    pieces = [p for s in sections if s for p in _split_long(s)]
    return [Chunk(meta, i, p) for i, p in enumerate(pieces)]
