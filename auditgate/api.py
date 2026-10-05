"""Local HTTP API so other tools (accounting exports, n8n, Zapier-on-prem) can call AuditGate.

Run:  uv run uvicorn auditgate.api:app --host 127.0.0.1 --port 8000
Binds to localhost by default - documents never need to cross the network.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from auditgate import __version__
from auditgate.agent.graph import ResolutionService
from auditgate.agent.resolver import build_model
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


# --------------------------------------------------------------------------- durable resolutions (Module 4)


class ResolutionRequest(BaseModel):
    text: str
    thread_id: str
    doc_type: DocumentType | None = None


class ApprovalWebhook(BaseModel):
    event_id: str  # the sender's unique delivery id - our idempotency key
    thread_id: str
    decision: Literal["approve", "reject"]
    approver: str
    note: str | None = None


def resolution_service() -> ResolutionService:
    """Created on first use. Tests (or other apps) can set app.state.resolutions themselves."""
    service = getattr(app.state, "resolutions", None)
    if service is None:
        settings = get_settings()
        try:
            model = build_model(settings)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        service = app.state.resolutions = ResolutionService(model, "auditgate_state.db",
                                                            allowed_domains=tuple(settings.egress_allowed_domains))
    return service


@app.post("/resolutions")
def start_resolution(req: ResolutionRequest) -> dict[str, Any]:
    report = process_text(req.text, req.doc_type)
    if report.status.value == "pass":
        return {"thread_id": req.thread_id, "state": "not_needed", "audit_status": "pass"}
    service = resolution_service()
    if service.status(req.thread_id)["state"] != "unknown":
        return service.status(req.thread_id)  # starting twice is a no-op
    return service.start(report, req.thread_id)


@app.get("/resolutions/{thread_id}")
def get_resolution(thread_id: str) -> dict[str, Any]:
    status = resolution_service().status(thread_id)
    if status["state"] == "unknown":
        raise HTTPException(status_code=404, detail="no such resolution")
    return status


@app.post("/webhooks/approval")
def approval_webhook(event: ApprovalWebhook) -> dict[str, Any]:
    decision = {"decision": event.decision, "approver": event.approver, "note": event.note}
    return resolution_service().resume(event.thread_id, decision, idempotency_key=event.event_id)
