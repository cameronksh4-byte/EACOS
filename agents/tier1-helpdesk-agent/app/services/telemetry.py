"""Tier 0: executive ROI and deflection telemetry.

Every triage execution writes one `ticket_telemetry` row in the background,
so logging never adds latency or fails a ticket. The ROI summary is computed
in SQL (`tenant_roi_summary`, `tenant_knowledge_gaps`), which counts each
ticket once even across multiple conversation turns.

Minutes saved are credited only to genuine deflections: in scope, resolved
with cited SOP steps, and not escalated. Outage broadcasts, declines, and
escalations earn nothing, and the database enforces this too.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import Feature, get_settings
from app.schemas.ticket import (
    AgentTriageResult,
    IncomingTicketWebhook,
    KnowledgeGap,
    ROISummary,
    TicketTelemetryEvent,
)
from app.services import background, supabase_client

logger = logging.getLogger(__name__)


def build_event(
    payload: IncomingTicketWebhook, result: AgentTriageResult, cost_usd: float, latency_ms: int
) -> TicketTelemetryEvent:
    settings = get_settings()
    deflected = result.deflected
    gap = (result.missing_sop_topic or "").strip() or None
    return TicketTelemetryEvent(
        tenant_id=payload.tenant_id,
        ticket_id=payload.ticket_id,
        thread_id=payload.thread_id,
        service_tier=settings.service_tier.value,
        category=result.category,
        action=result.action,
        scope=result.scope,
        confidence_score=round(result.confidence_score, 3),
        deflected_bool=deflected,
        estimated_minutes_saved=settings.minutes_saved_per_deflection if deflected else 0,
        missing_sop_topic=gap[:200] if gap else None,
        token_cost_usd=round(cost_usd, 6),
        outage_detected=result.active_outage_detected,
        parent_incident_id=result.parent_incident_id,
        model=result.model_used,
        latency_ms=max(latency_ms, 0),
    )


async def write_event(event: TicketTelemetryEvent) -> None:
    try:
        await supabase_client.insert("ticket_telemetry", event.model_dump(mode="json"))
    except Exception:  # noqa: BLE001 - telemetry must never break triage
        logger.exception("telemetry write failed ticket=%s", event.ticket_id)


def log_triage(payload: IncomingTicketWebhook, result: AgentTriageResult, cost_usd: float, latency_ms: int) -> None:
    """Fire-and-forget. No-op when ROI telemetry is disabled."""
    if not get_settings().feature_enabled(Feature.ROI_TELEMETRY):
        return
    event = build_event(payload, result, cost_usd, latency_ms)
    background.spawn(write_event(event), f"telemetry {payload.ticket_id}")


async def roi_summary(tenant_id: str, days: int = 30) -> ROISummary:
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    rows = await supabase_client.rpc(
        "tenant_roi_summary",
        {"p_tenant_id": tenant_id, "p_since": since.isoformat(), "p_until": until.isoformat()},
    )
    gaps = await supabase_client.rpc(
        "tenant_knowledge_gaps", {"p_tenant_id": tenant_id, "p_since": since.isoformat(), "p_limit": 5}
    )
    row = (rows[0] if isinstance(rows, list) and rows else rows) or {}
    return ROISummary(
        tenant_id=tenant_id,
        period_start=since,
        period_end=until,
        unique_tickets=int(row.get("unique_tickets") or 0),
        total_triages=int(row.get("total_triages") or 0),
        deflected_tickets=int(row.get("deflected_tickets") or 0),
        deflection_rate_pct=float(row.get("deflection_rate_pct") or 0.0),
        total_hours_saved=float(row.get("hours_saved") or 0.0),
        token_cost_usd=float(row.get("token_cost_usd") or 0.0),
        outage_tickets=int(row.get("outage_tickets") or 0),
        top_knowledge_gaps=[
            KnowledgeGap(topic=g["topic"], ticket_count=int(g["ticket_count"]), last_seen_at=g.get("last_seen_at"))
            for g in (gaps or [])[:5]
        ],
    )
