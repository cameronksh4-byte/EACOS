"""Strict input/output contracts for every service tier.

Three kinds of model live here:

* Inbound payloads (`IncomingTicketWebhook`, callbacks, resolved-ticket hooks):
  strict validation, extra fields rejected.
* LLM structured outputs (`LLM*`): every field required and no extras, so the
  model can't omit or invent fields. Numeric bounds are re-checked in code,
  because structured outputs turn `minimum`/`maximum` into description text.
* Results (`AgentTriageResult`): hard guardrails run in a model validator, so a
  non-compliant result can't be constructed anywhere in the codebase.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

CONFIDENCE_THRESHOLD = 0.80  # Hard guardrail #2. Settings may raise it, never lower it.
DOC_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# =============================================================================
# Enums
# =============================================================================


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
    IN_SCOPE = "in_scope"
    BEYOND_TIER1 = "beyond_tier1"  # IT, but needs Tier 2+ skills or write/admin access
    NOT_IT_SUPPORT = "not_it_support"  # HR, legal, homework, chit-chat, ...


class TriageAction(str, Enum):
    RESOLVE = "RESOLVE"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    AWAIT_MANAGER_APPROVAL = "AWAIT_MANAGER_APPROVAL"
    ESCALATE_TIER_2 = "ESCALATE_TIER_2"


class OSPlatform(str, Enum):
    UNKNOWN = "unknown"
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    IOS = "ios"
    ANDROID = "android"
    CHROMEOS = "chromeos"


class SOPPlatform(str, Enum):
    """Platform an SOP applies to. `any` is platform-agnostic."""

    ANY = "any"
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    IOS = "ios"
    ANDROID = "android"
    CHROMEOS = "chromeos"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class CatalogItemType(str, Enum):
    SOFTWARE = "software"
    HARDWARE = "hardware"


# =============================================================================
# Inbound ticket
# =============================================================================


class IncomingTicketWebhook(_Strict):
    tenant_id: str = Field(..., pattern=r"^[a-z0-9-]{2,64}$", description="Client namespace identifier")
    ticket_id: str = Field(..., min_length=1, max_length=128)
    thread_id: Optional[str] = Field(
        None, min_length=1, max_length=200, description="Conversation key for multi-turn (Tier 1+)"
    )
    reporter_email: str = Field(..., max_length=320)
    subject: str = Field(..., max_length=1000)
    body: str = Field(..., max_length=50_000)
    source_system: str = Field(default="slack", pattern="^(slack|teams|jira|zendesk|n8n|api)$")
    attachment_urls: List[HttpUrl] = Field(default_factory=list, max_length=10)

    @field_validator("reporter_email")
    @classmethod
    def _lower_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("attachment_urls")
    @classmethod
    def _https_only(cls, urls: List[HttpUrl]) -> List[HttpUrl]:
        for url in urls:
            if url.scheme != "https":
                raise ValueError("attachment URLs must use https")
        return urls

    @property
    def conversation_key(self) -> str:
        return self.thread_id or self.ticket_id


# =============================================================================
# Tier 0: vision + outage
# =============================================================================


class LLMVisionExtraction(_Strict):
    """Structured output of the screenshot-reading model."""

    error_codes: List[str] = Field(..., description="Exact error codes or numbers visible, verbatim; [] if none")
    dialog_text: str = Field(..., description="Verbatim text of any error dialog or banner; '' if none")
    application_name: str = Field(..., description="App or system shown, e.g. 'Outlook', 'GlobalProtect'; '' if unclear")
    ui_state: str = Field(..., description="One sentence describing what the screen shows")
    os_platform_hint: OSPlatform = Field(..., description="OS visible in the screenshot; 'unknown' if unclear")
    contains_sensitive_data: bool = Field(
        ..., description="True if a password, MFA code, card number or similar secret is visible"
    )


class VisionContext(_Strict):
    images_analyzed: int = Field(..., ge=0)
    images_skipped: int = Field(0, ge=0)
    extractions: List[LLMVisionExtraction] = Field(default_factory=list)

    def as_prompt_text(self) -> str:
        lines = []
        for i, e in enumerate(self.extractions, start=1):
            lines.append(
                f"Screenshot {i}: app={e.application_name or 'unknown'}; os={e.os_platform_hint.value}; "
                f"error_codes={', '.join(e.error_codes) or 'none'}; dialog={e.dialog_text or 'none'}; "
                f"state={e.ui_state}"
            )
        return "\n".join(lines)


class OutageMatch(_Strict):
    ticket_id: str
    similarity: float = Field(..., ge=-1.0, le=1.0)
    parent_incident_id: Optional[UUID] = None


# =============================================================================
# Tier 1: identity / MDM context
# =============================================================================


class HardwareAsset(_Strict):
    asset_tag: Optional[str] = Field(None, max_length=100)
    device_name: Optional[str] = Field(None, max_length=200)
    model: Optional[str] = Field(None, max_length=200)
    os_platform: OSPlatform = OSPlatform.UNKNOWN
    os_version: Optional[str] = Field(None, max_length=100)
    compliance_state: Optional[Literal["compliant", "noncompliant", "unknown"]] = None


class UserContext(_Strict):
    """Live identity/MDM facts about the reporter, injected before RAG (Tier 1+)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    email: str
    os_platform: OSPlatform = OSPlatform.UNKNOWN
    department: Optional[str] = Field(None, max_length=200)
    vip_status: bool = False
    assigned_hardware: List[HardwareAsset] = Field(default_factory=list, max_length=20)
    recent_auth_failures: int = Field(0, ge=0)
    manager_email: Optional[str] = Field(None, max_length=320)
    source: Literal["directory_cache", "microsoft_graph", "okta", "unavailable"] = "unavailable"

    @property
    def sop_platform_filter(self) -> Optional[SOPPlatform]:
        """Deterministic pgvector metadata filter. None means don't filter."""
        if self.os_platform is OSPlatform.UNKNOWN:
            return None
        return SOPPlatform(self.os_platform.value)

    def as_prompt_text(self) -> str:
        hw = "; ".join(
            f"{h.model or h.device_name or 'device'} ({h.os_platform.value} {h.os_version or ''})".strip()
            for h in self.assigned_hardware
        )
        return (
            f"os_platform={self.os_platform.value}; department={self.department or 'unknown'}; "
            f"vip={self.vip_status}; recent_auth_failures={self.recent_auth_failures}; "
            f"hardware={hw or 'none on record'}"
        )


# =============================================================================
# Triage: model output and API result
# =============================================================================


class LLMTriageOutput(_Strict):
    """The structured output Claude must produce for a ticket."""

    scope: ScopeVerdict = Field(
        ...,
        description="in_scope for Tier 1 work; beyond_tier1 for IT work needing Tier 2+/admin access; "
        "not_it_support for anything that is not an IT support request",
    )
    action: TriageAction = Field(
        ...,
        description="RESOLVE with cited steps; ASK_CLARIFICATION when one specific missing fact blocks "
        "a fix; AWAIT_MANAGER_APPROVAL for catalog software/hardware requests; ESCALATE_TIER_2 otherwise",
    )
    category: TicketCategory = Field(..., description="Use 'unknown' when scope is not in_scope")
    severity: SeverityLevel
    confidence_score: float = Field(..., description="0.0-1.0 confidence in the scope, category and reply")
    requires_tier2_escalation: bool = Field(
        ..., description="True if confidence < 0.80, no SOP covers the fix, or write/admin access is needed"
    )
    cited_sop_ids: List[str] = Field(
        ..., description="Exact doc_id values from <retrieved_sops> the reply relies on; [] if none"
    )
    draft_user_reply: str = Field(..., description="Friendly, numbered steps grounded only in the cited SOPs")
    clarifying_question: str = Field(
        ..., description="One short question when action is ASK_CLARIFICATION; '' otherwise"
    )
    requested_item: str = Field(
        ..., description="Software or hardware the user is asking for, as they named it; '' if none"
    )
    missing_sop_topic: str = Field(
        ..., description="Short topic (max 8 words) of the SOP that would have answered this; '' if covered"
    )
    internal_tech_notes: str = Field(..., description="Brief diagnostic summary for a technician; '' if none")


class AgentTriageResult(_Strict):
    """What the API returns, after deterministic guardrails.

    Hard guardrail #2 is enforced here, at construction, so no code path can
    return a non-compliant result. Escalation is forced when confidence < 0.80,
    the category is security_incident, or no SOP is cited, whenever the agent
    would show troubleshooting steps. Security incidents escalate whatever the
    outcome.

    Four outcomes give no troubleshooting steps and so are exempt, but only
    while the control that covers them is present. If that control is missing,
    the exemption is dropped (fail closed) and guardrail #2 applies:

    ===================  ==================================  ===========================
    Exempt outcome       Covered by                          Required on this model
    ===================  ==================================  ===========================
    Clarifying question  2-question cap in the DB            thread_id, question text,
                         (increment_clarification + CHECK)   1 <= clarifications_used <= 2
    Approval wait        approval_requests row: expires,     approval_id
                         escalates the thread on expiry
    Out-of-scope decline Fixed courteous template; no        (forced: no citations,
                         steps, nothing cited                category unknown)
    Outage broadcast     Parent incident escalated to        parent_incident_id
                         Tier 2 (outage_incidents)
    ===================  ==================================  ===========================

    Each override applied is recorded in `guardrail_overrides` for audit.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    ticket_id: str = ""
    thread_id: Optional[str] = None
    scope: ScopeVerdict = ScopeVerdict.IN_SCOPE
    action: TriageAction = TriageAction.RESOLVE
    category: TicketCategory
    severity: SeverityLevel
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    requires_tier2_escalation: bool = Field(
        ..., description="True if confidence is less than 0.80, policy is missing, or write-access is needed"
    )
    cited_sop_ids: List[str] = Field(default_factory=list, description="Exact document IDs used from RAG")
    draft_user_reply: str = Field(..., description="Step-by-step resolution grounded strictly in cited SOPs")
    internal_tech_notes: Optional[str] = Field(None, description="Diagnostic summary for Tier 2 handoff")
    clarifying_question: Optional[str] = None
    clarifications_used: int = Field(0, ge=0, le=2)
    approval_id: Optional[UUID] = None
    active_outage_detected: bool = False
    parent_incident_id: Optional[UUID] = None
    missing_sop_topic: Optional[str] = Field(None, max_length=200)
    service_tier: Optional[str] = None
    model_used: Optional[str] = None
    guardrail_overrides: List[str] = Field(default_factory=list)

    def _override(self, note: str) -> None:
        if note not in self.guardrail_overrides:
            self.guardrail_overrides.append(note)

    def _escalate(self, note: str) -> None:
        if self.action is not TriageAction.ESCALATE_TIER_2 or not self.requires_tier2_escalation:
            self._override(note)
        self.action = TriageAction.ESCALATE_TIER_2
        self.requires_tier2_escalation = True

    @model_validator(mode="after")
    def _hard_guardrails(self) -> "AgentTriageResult":
        # Out-of-scope decline: exempt, and forced to carry no steps or citations.
        if self.scope is ScopeVerdict.NOT_IT_SUPPORT:
            if self.cited_sop_ids:
                self._override("decline_citations_removed")
            self.cited_sop_ids = []
            self.category = TicketCategory.UNKNOWN
            self.action = TriageAction.RESOLVE
            self.requires_tier2_escalation = False
            self.active_outage_detected = False
            self.parent_incident_id = None
            self.clarifying_question = None
            self.approval_id = None
            return self

        if self.scope is ScopeVerdict.BEYOND_TIER1:
            self._escalate("beyond_tier1")
            return self

        # In scope. First drop any exemption whose covering control is missing.
        if self.active_outage_detected and self.parent_incident_id is None:
            self.active_outage_detected = False
            self._override("outage_without_parent_incident")

        if self.action is TriageAction.ASK_CLARIFICATION and not (
            self.clarifying_question and self.thread_id and 1 <= self.clarifications_used <= 2
        ):
            self._escalate("clarification_not_backed_by_capped_thread")

        if self.action is TriageAction.AWAIT_MANAGER_APPROVAL and self.approval_id is None:
            self._escalate("approval_wait_without_approval_record")

        # Security incidents always go to a human, whatever the outcome.
        if self.category is TicketCategory.SECURITY_INCIDENT:
            if not self.requires_tier2_escalation:
                self._override("security_incident")
            self.requires_tier2_escalation = True
            if self.action in (TriageAction.RESOLVE, TriageAction.AWAIT_MANAGER_APPROVAL) and not self.active_outage_detected:
                self.action = TriageAction.ESCALATE_TIER_2

        # Guardrail #2 for any outcome that shows troubleshooting steps.
        if self.action is TriageAction.RESOLVE and not self.active_outage_detected:
            if self.confidence_score < CONFIDENCE_THRESHOLD:
                self._escalate("confidence_below_threshold")
            elif not self.cited_sop_ids:
                self._escalate("no_cited_sop")
            elif self.requires_tier2_escalation:
                self._escalate("escalation_requested")

        if self.action is TriageAction.ESCALATE_TIER_2:
            self.requires_tier2_escalation = True
        return self

    @property
    def deflected(self) -> bool:
        """Resolved by the agent with grounded steps, no human needed."""
        return (
            self.scope is ScopeVerdict.IN_SCOPE
            and self.action is TriageAction.RESOLVE
            and not self.requires_tier2_escalation
        )


# =============================================================================
# Tier 1: approvals (n8n)
# =============================================================================


class CatalogItem(_Strict):
    tenant_id: str
    item_key: str
    item_type: CatalogItemType
    display_name: str
    aliases: List[str] = Field(default_factory=list)
    requires_manager_approval: bool = True
    approver_role: Literal["direct_manager", "app_owner", "it_manager"] = "direct_manager"
    app_owner_email: Optional[str] = None
    provisioning_group: Optional[str] = None
    license_cost_usd: Optional[float] = Field(None, ge=0)
    doc_id: Optional[str] = None
    active: bool = True


class ApprovalCardRequest(_Strict):
    """Signed payload we POST to n8n to render a Slack/Teams approval card."""

    event: Literal["approval.requested"] = "approval.requested"
    approval_id: UUID
    callback_nonce: str = Field(..., min_length=32)
    tenant_id: str
    ticket_id: str
    thread_id: str
    requester_email: str
    approver_email: str
    item_type: CatalogItemType
    item_key: str
    item_display_name: str
    justification: Optional[str] = Field(None, max_length=2000)
    license_cost_usd: Optional[float] = Field(None, ge=0)
    expires_at: datetime
    callback_url: HttpUrl
    source_system: str


class ApprovalCallback(_Strict):
    """What n8n POSTs to /v1/callbacks/approval when [Approve] or [Deny] is clicked."""

    approval_id: UUID
    callback_nonce: str = Field(..., min_length=32, max_length=128)
    tenant_id: str = Field(..., pattern=r"^[a-z0-9-]{2,64}$")
    decision: Literal["approved", "denied"]
    decided_by_email: str = Field(..., max_length=320)
    decision_note: Optional[str] = Field(None, max_length=2000)

    @field_validator("decided_by_email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not _EMAIL.match(value):
            raise ValueError("decided_by_email is not an email address")
        return value


class ApprovalOutcome(_Strict):
    approval_id: UUID
    status: ApprovalStatus
    provisioning_dispatched: bool
    message: str


# =============================================================================
# Tier 0: telemetry & ROI
# =============================================================================


class TicketTelemetryEvent(_Strict):
    tenant_id: str
    ticket_id: str
    thread_id: Optional[str] = None
    service_tier: str
    category: TicketCategory
    action: TriageAction
    scope: ScopeVerdict
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    deflected_bool: bool
    estimated_minutes_saved: int = Field(0, ge=0, le=480)
    missing_sop_topic: Optional[str] = Field(None, max_length=200)
    token_cost_usd: float = Field(0.0, ge=0.0)
    outage_detected: bool = False
    parent_incident_id: Optional[UUID] = None
    model: Optional[str] = None
    latency_ms: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def _minutes_only_when_deflected(self) -> "TicketTelemetryEvent":
        if not self.deflected_bool and self.estimated_minutes_saved:
            raise ValueError("estimated_minutes_saved must be 0 unless deflected")
        return self


class KnowledgeGap(_Strict):
    topic: str
    ticket_count: int = Field(..., ge=0)
    last_seen_at: Optional[datetime] = None


class ROISummary(_Strict):
    tenant_id: str
    period_start: datetime
    period_end: datetime
    unique_tickets: int = Field(..., ge=0)
    total_triages: int = Field(..., ge=0)
    deflected_tickets: int = Field(..., ge=0)
    deflection_rate_pct: float = Field(..., ge=0.0, le=100.0)
    total_hours_saved: float = Field(..., ge=0.0)
    token_cost_usd: float = Field(..., ge=0.0)
    outage_tickets: int = Field(0, ge=0)
    top_knowledge_gaps: List[KnowledgeGap] = Field(default_factory=list, max_length=5)


# =============================================================================
# Tier 2: knowledge flywheel
# =============================================================================


class TicketResolvedWebhook(_Strict):
    """Sent (e.g. by n8n or the ITSM) when a Tier 2 technician closes an escalated ticket."""

    tenant_id: str = Field(..., pattern=r"^[a-z0-9-]{2,64}$")
    ticket_id: str = Field(..., min_length=1, max_length=128)
    resolved_by_email: str = Field(..., max_length=320)
    subject: str = Field(..., max_length=1000)
    original_description: str = Field("", max_length=20_000)
    resolution_notes: str = Field(..., min_length=1, max_length=20_000)
    category: TicketCategory = TicketCategory.UNKNOWN
    system_name: Optional[str] = Field(None, max_length=200)
    os_platform: SOPPlatform = SOPPlatform.ANY
    resolved_at: Optional[datetime] = None


class LLMSOPSynthesis(_Strict):
    """Structured output of the SOP-writing model."""

    covers_knowledge_gap: bool = Field(
        ..., description="True only if the resolution teaches a reusable fix not already in the existing SOPs"
    )
    reasoning: str = Field(..., description="One or two sentences on why it is or isn't a gap")
    title: str = Field(..., description="Imperative SOP title, e.g. 'Fix Outlook error 0x800CCC0E'")
    system_name: str = Field(..., description="Primary system or application the SOP covers")
    os_platform: SOPPlatform
    markdown_body: str = Field(
        ...,
        description="Markdown SOP body WITHOUT frontmatter: When to use, Steps for the user, Escalate to "
        "Tier 2 when. No names, emails, hostnames, IPs, ticket IDs, or credentials.",
    )
    redactions_made: List[str] = Field(..., description="Kinds of data removed, e.g. ['email', 'hostname']")


class SOPFrontmatter(_Strict):
    doc_id: str = Field(..., pattern=DOC_ID_PATTERN)
    title: str
    system_name: str
    os_platform: SOPPlatform
    last_verified_date: date
    status: Literal["draft_pending_review"] = "draft_pending_review"
    source_ticket_id: str


class SOPDraft(_Strict):
    draft_id: Optional[UUID] = None
    tenant_id: str
    source_ticket_id: str
    frontmatter: SOPFrontmatter
    markdown: str = Field(..., max_length=50_000, description="Full document, YAML frontmatter included")
    nearest_doc_id: Optional[str] = None
    nearest_similarity: Optional[float] = Field(None, ge=-1.0, le=1.0)


class FlywheelOutcome(_Strict):
    ticket_id: str
    draft_created: bool
    draft_id: Optional[UUID] = None
    doc_id: Optional[str] = None
    reason: str
