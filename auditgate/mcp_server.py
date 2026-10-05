"""AuditGate as a Model Context Protocol (MCP) server.

Any MCP client (Claude Desktop, Claude Code, IDE agents, your own agent) can then
discover and call AuditGate's tools through one standard protocol instead of a
custom integration per client.

    uv run python -m auditgate.mcp_server          # stdio transport (what desktop clients launch)

Example client config (e.g. a desktop app's MCP settings):

    {"mcpServers": {"auditgate": {"command": "uv",
        "args": ["run", "--directory", "/path/to/EACOS", "python", "-m", "auditgate.mcp_server"]}}}

Privacy: an MCP client may itself be a hosted model, so everything this server
returns is sanitized first. Clients see placeholders, never raw PII.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from auditgate.agent.models import ToolCall
from auditgate.agent.resolver import SYSTEM_PROMPT, VendorStore, build_registry
from auditgate.config import Settings
from auditgate.extraction.schemas import DocumentType
from auditgate.pipeline import process_text
from auditgate.security.sanitizer import Sanitizer, seed_vault

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def build_server(store: VendorStore | None = None, settings: Settings | None = None) -> MCPServer:
    settings = settings or Settings(provider="heuristic")  # offline by default: the server never calls out
    store = store or VendorStore()
    registry = build_registry(store)
    sanitizer = Sanitizer(spacy_model=None, deny_terms=settings.deny_terms)
    server = MCPServer("auditgate", instructions="Audit invoices, bids and work orders locally. "
                                                 "Responses are sanitized: [[PLACEHOLDERS]] stand for private data.")

    def via_registry(name: str, arguments: dict[str, Any]) -> str:
        """Same validation and error feedback as the in-house agent: one tool implementation, two front doors."""
        result, _ = registry.execute(ToolCall(id="mcp", name=name, arguments=arguments))
        content = sanitizer.sanitize(result.content).text
        return f"ERROR: {content}" if result.is_error else content

    @server.tool(description="Audit a document's text: extract fields, check the math and dates, "
                             "flag injection attempts. Returns status (pass/review/fail), findings and data.",
                 annotations=READ_ONLY)
    def audit_document(text: str, doc_type: str | None = None) -> str:
        kind = DocumentType(doc_type) if doc_type else None
        report = process_text(text, kind, settings=settings, sanitizer=sanitizer)
        payload = {"doc_type": report.doc_type.value, "status": report.status.value,
                   "findings": [f.model_dump(mode="json") for f in report.findings], "data": report.data}
        # Mask with the vault built from the ORIGINAL document: a name found next to "Attn:" in the
        # source must stay masked when it reappears as a bare JSON value like "bill_to": "...".
        vault = seed_vault(report.data, sanitizer.sanitize(text).vault)
        return sanitizer.sanitize(json.dumps(payload, indent=2), vault).text

    @server.tool(description="Past invoices, average amount, prior disputes and notes for a vendor.",
                 annotations=READ_ONLY)
    def lookup_vendor_history(vendor_name: str) -> str:
        return via_registry("lookup_vendor_history", {"vendor_name": vendor_name})

    @server.tool(description="Approved purchase orders for a vendor.", annotations=READ_ONLY)
    def find_purchase_orders(vendor_name: str) -> str:
        return via_registry("find_purchase_orders", {"vendor_name": vendor_name})

    @server.tool(description="Exact quantity x unit_price, rounded to cents.", annotations=READ_ONLY)
    def line_total(quantity: str, unit_price: str) -> str:
        return via_registry("line_total", {"quantity": quantity, "unit_price": unit_price})

    @server.resource("auditgate://policy", mime_type="text/plain",
                     description="The rules AuditGate's resolver agent follows.")
    def policy() -> str:
        return SYSTEM_PROMPT

    @server.resource("auditgate://document-types", mime_type="application/json",
                     description="Document types audit_document accepts.")
    def document_types() -> str:
        return json.dumps([d.value for d in DocumentType])

    return server


if __name__ == "__main__":
    build_server().run("stdio")
