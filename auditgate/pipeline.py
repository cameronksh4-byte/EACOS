"""End-to-end orchestration: load -> sanitize -> extract -> rehydrate -> validate -> audit."""

from __future__ import annotations

import io
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from auditgate.config import Settings, get_settings
from auditgate.extraction.extractor import Extractor, build_extractor, detect_document_type
from auditgate.extraction.schemas import SCHEMAS, AuditFinding, DocumentType, Severity
from auditgate.security.sanitizer import Sanitizer


class Status(StrEnum):
    PASS = "pass"      # extracted cleanly, nothing to flag
    REVIEW = "review"  # extracted, but a human should look at warnings
    FAIL = "fail"      # schema invalid or audit errors - do not pay / do not approve


class AuditReport(BaseModel):
    doc_type: DocumentType
    extractor: str
    status: Status
    data: dict[str, Any] | None = None
    findings: list[AuditFinding] = Field(default_factory=list)
    redactions: dict[str, int] = Field(default_factory=dict)
    outbound_payload: str = Field(description="Exactly the text an extraction backend received")
    sent_offsite: bool = False


def load_text(source: str | Path | bytes, filename: str | None = None) -> str:
    """Turn a PDF, spreadsheet, CSV or text file into plain text. Runs entirely locally."""
    if isinstance(source, (str, Path)) and not isinstance(source, bytes):
        path = Path(source)
        filename = filename or path.name
        data = path.read_bytes()
    else:
        data = source
    suffix = Path(filename or "").suffix.lower()

    if suffix == ".pdf":
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    if suffix in (".xlsx", ".xlsm", ".xls", ".csv"):
        import pandas as pd

        reader = pd.read_csv if suffix == ".csv" else pd.read_excel
        frame = reader(io.BytesIO(data), header=None, dtype=str).fillna("")
        return "\n".join("  ".join(cell for cell in row if cell) for row in frame.itertuples(index=False))
    return data.decode("utf-8", errors="replace")


def _status(findings: list[AuditFinding]) -> Status:
    severities = {f.severity for f in findings}
    if Severity.ERROR in severities:
        return Status.FAIL
    if Severity.WARNING in severities:
        return Status.REVIEW
    return Status.PASS


def process_text(
    text: str,
    doc_type: DocumentType | None = None,
    *,
    settings: Settings | None = None,
    extractor: Extractor | None = None,
    sanitizer: Sanitizer | None = None,
) -> AuditReport:
    settings = settings or get_settings()
    extractor = extractor or build_extractor(settings)
    sanitizer = sanitizer or Sanitizer.from_settings(settings)
    doc_type = doc_type or detect_document_type(text)

    sanitized = sanitizer.sanitize(text)
    report = AuditReport(
        doc_type=doc_type, extractor=extractor.name, status=Status.FAIL,
        redactions=sanitized.vault.counts, outbound_payload=sanitized.text,
        sent_offsite=extractor.name in ("openai", "anthropic"),
    )
    try:
        raw = extractor.extract(sanitized.text, doc_type)
    except Exception as exc:  # network errors, retries exhausted, malformed output
        report.findings.append(AuditFinding(code="EXTRACTION_FAILED", severity=Severity.ERROR,
                                            message=f"{type(exc).__name__}: {exc}"))
        return report

    try:
        model = SCHEMAS[doc_type].model_validate(sanitized.vault.rehydrate(raw))
    except ValidationError as exc:
        report.data = sanitized.vault.rehydrate(raw)
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "<root>"
            report.findings.append(AuditFinding(code="SCHEMA_INVALID", severity=Severity.ERROR,
                                                message=f"{loc}: {err['msg']}"))
        return report

    report.data = model.model_dump(mode="json")
    report.findings = model.audit()
    report.status = _status(report.findings)
    return report


def process_file(path: str | Path, doc_type: DocumentType | None = None, **kwargs: Any) -> AuditReport:
    return process_text(load_text(path), doc_type, **kwargs)
