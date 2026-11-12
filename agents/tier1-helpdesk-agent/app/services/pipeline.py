"""One entry point for every channel: budget -> RAG -> model routing -> guardrails."""

from __future__ import annotations

import logging

from app.config import get_settings
from app.schemas.ticket import AgentTriageResult, IncomingTicketWebhook
from app.services import guardrails, llm_router, rag, usage

logger = logging.getLogger(__name__)


async def process_ticket(payload: IncomingTicketWebhook) -> AgentTriageResult:
    settings = get_settings()

    if await usage.budget_exhausted(payload.tenant_id):
        logger.warning("monthly token budget reached tenant=%s ticket=%s", payload.tenant_id, payload.ticket_id)
        return guardrails.unavailable_result(payload, settings, "monthly token budget reached")

    # 1. Grounded context from the tenant's own pgvector namespace.
    try:
        sops = await rag.retrieve_relevant_sops(
            tenant_id=payload.tenant_id,
            query=f"{payload.subject}\n{payload.body}",
            top_k=settings.rag_top_k,
        )
    except Exception:  # noqa: BLE001
        # Keep going without SOPs: out-of-scope requests still get declined,
        # and in-scope ones escalate because nothing can be cited.
        logger.exception("sop retrieval failed ticket=%s", payload.ticket_id)
        sops = []

    # 2. Haiku -> (maybe) Sonnet, with schema-enforced output.
    try:
        decision = await llm_router.route_ticket(payload, sops)
    except llm_router.LLMUnavailableError as exc:
        logger.error("llm unavailable ticket=%s err=%s", payload.ticket_id, exc)
        await _record(payload, exc.attempts)
        return guardrails.unavailable_result(payload, settings, "language model unavailable")
    await _record(payload, decision.attempts)

    # 3. Deterministic guardrails.
    result = guardrails.enforce_policy(
        payload,
        decision.output,
        retrieved_sop_ids={s.sop_id for s in sops},
        settings=settings,
        model_used=decision.model,
    )
    logger.info(
        "triaged ticket=%s source=%s scope=%s category=%s severity=%s escalate=%s model=%s",
        payload.ticket_id,
        payload.source_system,
        result.scope.value,
        result.category.value,
        result.severity.value,
        result.requires_tier2_escalation,
        decision.model,
    )
    return result


async def _record(payload: IncomingTicketWebhook, attempts: list[llm_router.ModelAttempt]) -> None:
    for attempt in attempts:
        if attempt.usage is not None:
            await usage.record_usage(payload.tenant_id, payload.ticket_id, attempt.model, attempt.usage)
