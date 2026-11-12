"""One entry point for every channel.

Tier 0 flow:

    trial check
      -> embed ticket text  -> outage burst check (30 min)  --match--> broadcast, no model call
      -> screenshot vision (if attachments) -> re-embed with findings
      -> [Tier 1: identity/MDM context -> os_platform filter]      (Phase 3)
      -> SOP retrieval (tenant-filtered pgvector)
      -> Haiku -> (maybe) Sonnet, each call budget-gated
      -> deterministic guardrails + schema validator
      -> telemetry (background)

Outage detection runs on the text embedding *before* vision, so a ticket in an
outage costs one embedding call and no model tokens. Every failure degrades
toward a human: no SOPs means no citations, which means escalation.
"""

from __future__ import annotations

import logging
import time

from app.config import Settings, get_settings
from app.schemas.ticket import AgentTriageResult, IncomingTicketWebhook, ScopeVerdict, TicketCategory
from app.services import background, guardrails, llm_client, llm_router, outage, rag, telemetry, vision

logger = logging.getLogger(__name__)


async def process_ticket(payload: IncomingTicketWebhook) -> AgentTriageResult:
    settings = get_settings()
    started = time.monotonic()
    ledger = llm_client.CostLedger()

    result = await _triage(payload, settings, ledger)

    # Only in-scope IT categories can later mark a cluster as an outage.
    if (not result.active_outage_detected and result.scope is ScopeVerdict.IN_SCOPE
            and result.category is not TicketCategory.UNKNOWN):
        background.spawn(
            outage.record_category(payload.tenant_id, payload.ticket_id, result.category),
            f"outage-category {payload.ticket_id}",
        )
    telemetry.log_triage(payload, result, ledger.total_usd, int((time.monotonic() - started) * 1000))
    logger.info(
        "triaged ticket=%s tier=%s scope=%s action=%s category=%s escalate=%s outage=%s cost=%.5f model=%s",
        payload.ticket_id, settings.service_tier.value, result.scope.value, result.action.value,
        result.category.value, result.requires_tier2_escalation, result.active_outage_detected,
        ledger.total_usd, result.model_used,
    )
    return result


async def _triage(
    payload: IncomingTicketWebhook, settings: Settings, ledger: llm_client.CostLedger
) -> AgentTriageResult:
    if settings.trial_expired():
        return guardrails.unavailable_result(payload, settings, "trial period ended")

    # 1. Embed the ticket text once; it drives outage matching and SOP search.
    text_query = rag.build_query_text(payload, None)
    embedding: list[float] | None
    try:
        embedding = await rag.embed_query(text_query)
    except Exception:  # noqa: BLE001
        logger.exception("embedding failed ticket=%s", payload.ticket_id)
        embedding = None

    # 2. Outage burst detection, before any SOP lookup or model call.
    if embedding is not None:
        try:
            burst = await outage.record_and_detect(payload, embedding, summary=payload.subject)
        except Exception:  # noqa: BLE001
            logger.exception("outage detection failed ticket=%s", payload.ticket_id)
        else:
            if burst.active and burst.incident_id is not None:
                return guardrails.outage_result(
                    payload, settings, burst.incident_id, burst.category, burst.matched_tickets
                )

    # 3. Screenshots: exact error codes and dialog text, before retrieval.
    try:
        screenshots = await vision.analyze_attachments(payload, ledger)
    except (llm_client.BudgetExceededError, llm_client.BudgetUnavailableError) as exc:
        return guardrails.unavailable_result(payload, settings, _budget_reason(exc))
    if screenshots and screenshots.extractions:
        try:
            embedding = await rag.embed_query(rag.build_query_text(payload, screenshots))
        except Exception:  # noqa: BLE001
            logger.exception("re-embedding with screenshot findings failed ticket=%s", payload.ticket_id)

    # 4. Tier 1 context enrichment hooks in here (Phase 3); Tier 0 doesn't filter by OS.
    os_filter: str | None = None

    # 5. Grounded context from the tenant's own SOPs.
    sops: list[rag.SopChunk] = []
    if embedding is not None:
        try:
            sops = await rag.retrieve_relevant_sops(
                payload.tenant_id, top_k=settings.rag_top_k, os_platform=os_filter, embedding=embedding
            )
        except Exception:  # noqa: BLE001
            # Keep going: out-of-scope requests still get declined, in-scope ones
            # escalate because nothing can be cited.
            logger.exception("sop retrieval failed ticket=%s", payload.ticket_id)

    # 6. Model routing. Every call is budget-gated inside llm_client.
    try:
        decision = await llm_router.route_ticket(payload, sops, ledger=ledger, vision=screenshots)
    except (llm_client.BudgetExceededError, llm_client.BudgetUnavailableError) as exc:
        return guardrails.unavailable_result(payload, settings, _budget_reason(exc))
    except llm_router.LLMUnavailableError as exc:
        logger.error("llm unavailable ticket=%s err=%s", payload.ticket_id, exc)
        return guardrails.unavailable_result(payload, settings, "language model unavailable")

    # 7. Deterministic guardrails (the result validator enforces guardrail #2).
    return guardrails.enforce_policy(
        payload, decision.output, {s.doc_id for s in sops}, settings, decision.model
    )


def _budget_reason(exc: Exception) -> str:
    if isinstance(exc, llm_client.BudgetExceededError):
        return "monthly token budget reached"
    return "budget ledger unavailable, so the spend cap couldn't be verified"
