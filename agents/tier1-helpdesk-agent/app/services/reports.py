"""Tier 2: automated weekly executive ROI and knowledge-gap report.

n8n triggers POST /v1/reports/weekly on its schedule (e.g. Mondays 08:00), rather
than a timer in this service. With several ECS tasks running, an internal timer
would send duplicate reports. The report is plain numbers from SQL, rendered
deterministically (no model call, no cost), and delivered through the Tier 2
notification channel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Feature, get_settings
from app.gating import gated
from app.schemas.ticket import ROISummary, WeeklyReport
from app.services import notifier, supabase_client, telemetry


def _delta(now: float, before: float, unit: str = "") -> str:
    diff = now - before
    sign = "+" if diff >= 0 else "-"
    return f"{sign}{abs(diff):.1f}{unit}"


def render(tenant_id: str, roi: ROISummary, prev: ROISummary, drafts: int, outages: int) -> str:
    settings = get_settings()
    lines = [
        f"# {settings.helpdesk_name}: weekly AI help desk report ({tenant_id})",
        f"_{roi.period_start:%d %b %Y} to {roi.period_end:%d %b %Y}_",
        "",
        "| Metric | This week | vs last week |",
        "|---|---|---|",
        f"| Tickets handled | {roi.unique_tickets} | {_delta(roi.unique_tickets, prev.unique_tickets)} |",
        f"| Resolved without a technician | {roi.deflected_tickets} ({roi.deflection_rate_pct:.1f}%) | "
        f"{_delta(roi.deflection_rate_pct, prev.deflection_rate_pct, ' pts')} |",
        f"| Technician hours saved | {roi.total_hours_saved:.1f} h | {_delta(roi.total_hours_saved, prev.total_hours_saved, ' h')} |",
        f"| Tickets absorbed by outage detection | {roi.outage_tickets} ({outages} incidents) | "
        f"{_delta(roi.outage_tickets, prev.outage_tickets)} |",
        f"| AI cost | ${roi.token_cost_usd:.2f} | {_delta(roi.token_cost_usd, prev.token_cost_usd)} |",
        "",
        "## Top knowledge gaps (no SOP covered these)",
    ]
    if roi.top_knowledge_gaps:
        lines += [f"{i}. {g.topic} ({g.ticket_count} tickets)" for i, g in enumerate(roi.top_knowledge_gaps, 1)]
    else:
        lines.append("None this week.")
    lines += ["", f"SOP drafts waiting for your review: **{drafts}**",
              "", f"_Hours saved assumes {settings.minutes_saved_per_deflection} minutes per resolved ticket._"]
    return "\n".join(lines)


async def _count(table: str, filters: dict[str, str]) -> int:
    rows = await supabase_client.select(table, filters, columns="tenant_id", limit=1000)
    return len(rows)


@gated(Feature.EXECUTIVE_REPORTS, default=None)
async def weekly_report(tenant_id: str, deliver: bool = True) -> WeeklyReport | None:
    now = datetime.now(timezone.utc)
    roi = await telemetry.roi_summary(tenant_id, days=7, until=now)
    # The previous week is queried as its own window. (Subtracting this week
    # from a 14-day total is wrong: unique tickets and rates aren't additive.)
    prev = await telemetry.roi_summary(tenant_id, days=7, until=now - timedelta(days=7))

    since = (now - timedelta(days=7)).isoformat()
    drafts = await _count("sop_drafts", {"tenant_id": supabase_client.eq(tenant_id), "status": "eq.draft_pending_review"})
    outages = await _count("outage_incidents", {"tenant_id": supabase_client.eq(tenant_id),
                                                "first_seen_at": f"gte.{since}"})
    markdown = render(tenant_id, roi, prev, drafts, outages)
    delivered = False
    if deliver:
        delivered = await notifier.notify_tier2(
            "report.weekly", tenant_id,
            {"roi": roi.model_dump(mode="json"), "previous": prev.model_dump(mode="json"),
             "sop_drafts_pending_review": drafts, "outage_incidents": outages, "markdown": markdown},
            markdown,
        )
    return WeeklyReport(tenant_id=tenant_id, generated_at=datetime.now(timezone.utc), roi=roi, previous_roi=prev,
                        sop_drafts_pending_review=drafts, outage_incidents=outages, markdown=markdown,
                        delivered=delivered)
