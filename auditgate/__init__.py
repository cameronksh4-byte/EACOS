"""AuditGate: local-first document extraction and audit engine.

Pipeline: load -> sanitize (PII never leaves the box) -> extract (structured output)
-> rehydrate -> validate (Pydantic) -> audit (deterministic rules) -> report.
"""

__version__ = "0.1.0"
