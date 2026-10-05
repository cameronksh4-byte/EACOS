"""AuditGate local dashboard.

    uv run streamlit run auditgate/ui.py

Runs on localhost. With the default `heuristic` provider nothing leaves the machine.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow `streamlit run auditgate/ui.py`

from auditgate.config import get_settings  # noqa: E402
from auditgate.evals.runner import load_cases  # noqa: E402
from auditgate.extraction.schemas import DocumentType, Severity  # noqa: E402
from auditgate.pipeline import Status, load_text, process_text  # noqa: E402

STATUS_BADGE = {
    Status.PASS: ("✅ PASS", "success", "Everything reconciles. Safe to approve."),
    Status.REVIEW: ("🟡 NEEDS REVIEW", "warning", "Extracted, but a human should check the warnings."),
    Status.FAIL: ("⛔ FAIL", "error", "Do not approve or pay until the errors below are resolved."),
}
SEVERITY_ICON = {Severity.ERROR: "⛔", Severity.WARNING: "🟡", Severity.INFO: "ℹ️"}

st.set_page_config(page_title="AuditGate", page_icon="🛡️", layout="wide")
settings = get_settings()

with st.sidebar:
    st.title("🛡️ AuditGate")
    st.caption("Local-first document audit")
    offsite = settings.sends_data_offsite
    st.metric("Extraction engine", settings.provider)
    if offsite:
        st.warning("Hosted model: only **sanitized** text is sent. Originals stay on this machine.")
    else:
        st.success("Fully local: no document data leaves this machine.")
    doc_choice = st.selectbox("Document type", ["Auto-detect", *[d.value for d in DocumentType]])
    st.divider()
    st.caption("Try a sample from the test suite")
    samples = {c["id"]: c for c in load_cases()}
    sample_id = st.selectbox("Sample", ["(none)", *samples])

st.header("Audit a document")
upload = st.file_uploader("Upload PDF, Excel, CSV or text", type=["pdf", "xlsx", "xls", "csv", "txt", "md"])
default_text = samples[sample_id]["text"] if sample_id != "(none)" else ""
pasted = st.text_area("...or paste the document text", value=default_text, height=220)

text = load_text(upload.getvalue(), upload.name) if upload is not None else pasted

if st.button("Run audit", type="primary", disabled=not text.strip()):
    doc_type = None if doc_choice == "Auto-detect" else DocumentType(doc_choice)
    with st.spinner("Sanitizing, extracting and auditing..."):
        report = process_text(text, doc_type, settings=settings)

    label, kind, blurb = STATUS_BADGE[report.status]
    getattr(st, kind)(f"**{label}** · {report.doc_type.value.replace('_', ' ').title()} · {blurb}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Findings", len(report.findings))
    c2.metric("Sensitive values masked", sum(report.redactions.values()))
    c3.metric("Sent off-site", "Yes (sanitized)" if report.sent_offsite else "No")

    st.subheader("Findings")
    if not report.findings:
        st.write("No issues found.")
    for f in report.findings:
        st.markdown(f"{SEVERITY_ICON[f.severity]} **{f.code}** - {f.message}")

    left, right = st.columns(2)
    with left:
        st.subheader("Extracted data")
        if report.data:
            st.json(report.data)
            items = report.data.get("line_items") or report.data.get("scope_items")
            if items:
                st.dataframe(items, width="stretch")
    with right:
        st.subheader("What the AI engine saw")
        st.caption("Sensitive values replaced with placeholders before extraction.")
        st.code(report.outbound_payload, language="text")
        if report.redactions:
            st.write({k.replace("_", " ").title(): v for k, v in report.redactions.items()})

    st.download_button("Download audit report (JSON)", data=json.dumps(report.model_dump(mode="json"), indent=2),
                       file_name=f"auditgate_{report.doc_type.value}.json", mime="application/json")
