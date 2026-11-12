"""Tier 0: 30-minute outage burst detection.

Before any SOP lookup, the ticket's embedding is stored in `recent_tickets` and
compared with the same tenant's tickets from the last 30 minutes. If at least
OUTAGE_MIN_MATCHING_TICKETS (5) match at cosine similarity >=
OUTAGE_SIMILARITY_THRESHOLD (0.88), the ticket joins an outage incident. It
gets an immediate broadcast reply, and the triage model isn't called at all.

Guardrail #2 exemption control: a broadcast doesn't escalate each ticket,
because the parent incident goes to Tier 2. Delivery is recorded
(`tier2_notified_at`) and retried until it succeeds.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from app.config import Feature, get_settings
from app.schemas.ticket import IncomingTicketWebhook, OutageMatch, TicketCategory
from app.services import background, notifier, supabase_client

logger = logging.getLogger(__name__)


@dataclass
class OutageDecision:
    active: bool
    matched_tickets: int = 0
    incident_id: UUID | None = None
    is_new_incident: bool = False
    category: TicketCategory = TicketCategory.UNKNOWN
    matches: list[OutageMatch] = field(default_factory=list)


def _dominant_category(categories: list[str | None]) -> TicketCategory:
    known = [c for c in categories if c and c != TicketCategory.UNKNOWN.value]
    if TicketCategory.SECURITY_INCIDENT.value in known:
        return TicketCategory.SECURITY_INCIDENT  # one security report taints the cluster
    if not known:
        return TicketCategory.UNKNOWN
    try:
        return TicketCategory(Counter(known).most_common(1)[0][0])
    except ValueError:
        return TicketCategory.UNKNOWN


async def record_and_detect(
    payload: IncomingTicketWebhook, embedding: list[float], summary: str
) -> OutageDecision:
    settings = get_settings()
    if not settings.feature_enabled(Feature.OUTAGE_DETECTION):
        return OutageDecision(active=False)

    await supabase_client.upsert(
        "recent_tickets",
        {
            "tenant_id": payload.tenant_id,
            "ticket_id": payload.ticket_id,
            "thread_id": payload.thread_id,
            "summary": summary[:500],
            "embedding": embedding,
        },
        on_conflict="tenant_id,ticket_id",
    )
    rows = await supabase_client.rpc(
        "match_recent_tickets",
        {
            "p_tenant_id": payload.tenant_id,
            "p_query_embedding": embedding,
            "p_window_minutes": settings.outage_window_minutes,
            "p_min_similarity": settings.outage_similarity_threshold,
            "p_exclude_ticket_id": payload.ticket_id,
            "p_limit": 200,
        },
    )
    matches = [OutageMatch(ticket_id=r["ticket_id"], similarity=r["similarity"],
                           parent_incident_id=r.get("parent_incident_id")) for r in rows]
    if len(matches) < settings.outage_min_matching_tickets:
        return OutageDecision(active=False, matched_tickets=len(matches), matches=matches)

    # Only a cluster the model has already seen as in-scope IT can be an outage.
    # Five similar payroll questions are not a service outage.
    category = _dominant_category([r.get("category") for r in rows])
    if category is TicketCategory.UNKNOWN:
        logger.info("similar-ticket cluster ignored: no triaged IT category yet tenant=%s matched=%d",
                    payload.tenant_id, len(matches))
        return OutageDecision(active=False, matched_tickets=len(matches), matches=matches)

    registered = await supabase_client.rpc(
        "register_outage_cluster",
        {
            "p_tenant_id": payload.tenant_id,
            "p_ticket_id": payload.ticket_id,
            "p_matched_ticket_ids": [m.ticket_id for m in matches],
            "p_title": payload.subject[:200],
        },
    )
    row = registered[0] if isinstance(registered, list) else registered
    decision = OutageDecision(
        active=True,
        matched_tickets=len(matches),
        incident_id=UUID(str(row["incident_id"])),
        is_new_incident=bool(row["is_new"]),
        category=category,
        matches=matches,
    )
    logger.warning(
        "outage burst tenant=%s incident=%s matched=%d new=%s category=%s",
        payload.tenant_id, decision.incident_id, decision.matched_tickets,
        decision.is_new_incident, decision.category.value,
    )
    if row.get("needs_tier2_notification"):
        background.spawn(
            notify_incident(payload.tenant_id, decision.incident_id, decision.matched_tickets + 1,
                            decision.category, payload.subject),
            f"outage-notify {decision.incident_id}",
        )
    return decision


async def notify_incident(
    tenant_id: str, incident_id: UUID, ticket_count: int, category: TicketCategory, sample_subject: str
) -> bool:
    settings = get_settings()
    text = (
        f":rotating_light: Possible outage for {tenant_id}: {ticket_count} similar tickets in "
        f"{settings.outage_window_minutes} min. Incident {incident_id}. Category: {category.value}. "
        f"Example subject: {sample_subject[:150]}"
    )
    delivered = await notifier.notify_tier2(
        "outage.detected",
        tenant_id,
        {"incident_id": str(incident_id), "ticket_count": ticket_count, "category": category.value,
         "window_minutes": settings.outage_window_minutes},
        text,
    )
    if delivered:
        await supabase_client.rpc(
            "mark_outage_tier2_notified", {"p_tenant_id": tenant_id, "p_incident_id": str(incident_id)}
        )
    return delivered


async def retry_pending_notifications() -> int:
    """Deliver any outage notifications that failed earlier. Returns how many succeeded."""
    settings = get_settings()
    delivered = 0
    for tenant_id in settings.allowed_tenant_ids:
        rows = await supabase_client.select(
            "outage_incidents",
            {"tenant_id": supabase_client.eq(tenant_id), "status": "eq.active", "tier2_notified_at": "is.null"},
            columns="incident_id,title,ticket_count",
            limit=20,
        )
        for row in rows:
            if await notify_incident(tenant_id, UUID(row["incident_id"]), row["ticket_count"],
                                     TicketCategory.UNKNOWN, row["title"]):
                delivered += 1
    return delivered


async def record_category(tenant_id: str, ticket_id: str, category: TicketCategory) -> None:
    """Remember a triaged ticket's category so later cluster members inherit it."""
    if not get_settings().feature_enabled(Feature.OUTAGE_DETECTION):
        return
    await supabase_client.update(
        "recent_tickets",
        {"tenant_id": supabase_client.eq(tenant_id), "ticket_id": supabase_client.eq(ticket_id)},
        {"category": category.value},
    )
