"""Strict input/output models for the Tier 1 agent."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TicketCategory(str, Enum):
    PASSWORD_AUTH = "password_auth"
    VPN_NETWORK = "vpn_network"
    HARDWARE_PRINTER = "hardware_printer"
    SOFTWARE_ACCESS = "software_access"
    SECURITY_INCIDENT = "security_incident"
    UNKNOWN = "unknown"


class SeverityLevel(str, Enum):
    LOW = "P4_LOW"
    MEDIUM = "P3_MEDIUM"
    HIGH = "P2_HIGH"
    CRITICAL = "P1_CRITICAL"


class ScopeVerdict(str, Enum):
    """Whether a request is something the Tier 1 desk handles at all."""

    IN_SCOPE = "in_scope"
    # An IT request, but it needs Tier 2+ skills or write/admin access.
    BEYOND_TIER1 = "beyond_tier1"
    # Not an IT support request (HR, legal, homework, chit-chat, ...).
    NOT_IT_SUPPORT = "not_it_support"


class IncomingTicketWebhook(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tenant_id: str = Field(..., min_length=1, max_length=64, description="Client namespace identifier")
    ticket_id: str = Field(..., min_length=1, max_length=128)
    reporter_email: str = Field(..., max_length=320)
    subject: str = Field(..., max_length=1000)
    body: str = Field(..., max_length=50_000)
    source_system: str = Field(default="slack", pattern="^(slack|teams|jira|zendesk)$")


class AgentTriageResult(BaseModel):
    """What the API returns. Built from the model's draft *after* guardrails run."""

    scope: ScopeVerdict = ScopeVerdict.IN_SCOPE
    category: TicketCategory
    severity: SeverityLevel
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    requires_tier2_escalation: bool = Field(
        ..., description="True if confidence is less than 0.80, policy is missing, or write-access is needed"
    )
    cited_sop_ids: List[str] = Field(default_factory=list, description="Exact document IDs used from RAG")
    draft_user_reply: str = Field(..., description="Step-by-step resolution grounded strictly in cited SOPs")
    internal_tech_notes: Optional[str] = Field(None, description="Diagnostic summary for Tier 2 handoff")


class LLMTriageOutput(BaseModel):
    """The structured output Claude must produce.

    Every field is required so the model can't silently omit one. Numeric
    bounds are re-checked (and clamped) in code, because structured outputs
    move `minimum`/`maximum` into the description text instead of enforcing them.
    """

    model_config = ConfigDict(extra="forbid")

    scope: ScopeVerdict = Field(
        ...,
        description="in_scope for Tier 1 work; beyond_tier1 for IT work needing Tier 2+/admin access; "
        "not_it_support for anything that is not an IT support request",
    )
    category: TicketCategory = Field(..., description="Use 'unknown' when scope is not in_scope")
    severity: SeverityLevel
    confidence_score: float = Field(..., description="0.0-1.0 confidence in the scope, category and reply")
    requires_tier2_escalation: bool = Field(
        ..., description="True if confidence < 0.80, no SOP covers the fix, or write/admin access is needed"
    )
    cited_sop_ids: List[str] = Field(
        ..., description="Exact sop_id values from <retrieved_sops> that the reply relies on; empty if none"
    )
    draft_user_reply: str = Field(
        ..., description="Friendly, numbered, step-by-step reply grounded only in the cited SOPs"
    )
    internal_tech_notes: str = Field(
        ..., description="Short diagnostic summary for a technician; empty string if nothing to add"
    )
