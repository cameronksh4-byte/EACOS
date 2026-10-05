"""Local HTTP API so other tools (accounting exports, n8n, Zapier-on-prem) can call AuditGate.

Run:  uv run uvicorn auditgate.api:app --host 127.0.0.1 --port 8000
Binds to localhost by default - documents never need to cross the network.
"""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from auditgate import __version__
from auditgate.config import get_settings
from auditgate.extraction.schemas import DocumentType
from auditgate.pipeline import AuditReport, load_text, process_text

app = FastAPI(title="AuditGate", version=__version__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class TextRequest(BaseModel):
    text: str
    doc_type: DocumentType | None = None


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "provider": settings.provider, "version": __version__}


@app.post("/audit/text", response_model=AuditReport)
def audit_text(req: TextRequest) -> AuditReport:
    return process_text(req.text, req.doc_type)


@app.post("/audit/file", response_model=AuditReport)
async def audit_file(file: UploadFile = File(...), doc_type: DocumentType | None = None) -> AuditReport:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    return process_text(load_text(data, file.filename), doc_type)
