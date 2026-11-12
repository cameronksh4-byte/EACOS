"""One entry point for every channel, every tier.

    trial check
    [T1] load conversation (tenant_id, thread_id)
         escalated thread       -> "added to your ticket", no model call
         awaiting approval      -> status reply, no model call
    embed ticket text (+ original issue on follow-ups)
    outage burst check, first message only  --match--> broadcast, no model call
    screenshot vision -> re-embed with findings
    [T1] identity/MDM context -> deterministic os_platform SOP filter
    SOP retrieval (tenant-filtered pgvector)
    Haiku -> (maybe) Sonnet, each call budget-gated
    [T1] ASK_CLARIFICATION   -> spend a DB-capped question, or escalate at the cap
    [T1] software/hardware   -> catalog -> provision | approval card via n8n | escalate
    deterministic guardrails + schema validator (guardrail #2 and its exemptions)
    [T1] persist the turn and slots; notify Tier 2 on escalation
    telemetry (background)

Each [T1] step is behind a tier gate (`@gated` or `feature_enabled`), so a
Tier 0 deployment runs exactly the Tier 0 flow. Every failure degrades toward
a human.
"""

from __future__ import annotations

import logging
import time
from uuid import UUID

from app.config import Feature, Settings, get_settings
from app.schemas.ticket import (
    AgentTriageResult,
    IncomingTicketWebhook,
    LLMTriageOutput,
    ScopeVerdict,
    TicketCategory,
    TriageAction,
    UserContext,
)
from app.services import (
    approvals,
    background,
    context_enrichment,
    conversation,
    guardrails,
    llm_client,
    llm_router,
    outage,
    rag,
    supabase_client,
    telemetry,
    vision,
)

logger = logging.getLogger(__name__)


async def process_ticket(payload: IncomingTicketWebhook) -> AgentTriageResult:
    settings = get_settings()
    started = time.monotonic()
    ledger = llm_client.CostLedger()

    state: conversation.ThreadState | None = None
    if conversation.multi_turn_enabled(payload):
        try:
            state = await conversation.load(payload)
        except Exception:  # noqa: BLE001
            logger.exception("conversation load failed ticket=%s; continuing single-turn", payload.ticket_id)

    result, slots = await _triage(payload, settings, ledger, state)

    if state is not None:
        background.spawn(_persist_turn(state, payload, result, slots), f"conversation {state.thread_id}")
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


async def _persist_turn(state: conversation.ThreadState, payload: IncomingTicketWebhook,
                        result: AgentTriageResult, slots: dict[str, str]) -> None:
    was_escalated = state.status == "escalated"
    await conversation.record_turn(state, payload, result, slots)
    if result.action is TriageAction.ESCALATE_TIER_2:
        reason = "requester added information" if was_escalated else (
            ", ".join(result.guardrail_overrides) or "agent escalation")
        await conversation.notify_escalation(state.tenant_id, state.thread_id, payload.ticket_id, reason,
                                             details=result.internal_tech_notes)


async def _pending_approval(state: conversation.ThreadState) -> dict | None:
    rows = await supabase_client.select(
        "approval_requests",
        {"tenant_id": supabase_client.eq(state.tenant_id), "thread_id": supabase_client.eq(state.thread_id),
         "status": "eq.pending"},
        columns="approval_id,item_display_name", limit=1,
    )
    return rows[0] if rows else None


async def _triage(
    payload: IncomingTicketWebhook,
    settings: Settings,
    ledger: llm_client.CostLedger,
    state: conversation.ThreadState | None,
) -> tuple[AgentTriageResult, dict[str, str]]:
    slots = dict(state.slots) if state else {}
    if settings.trial_expired():
        return guardrails.unavailable_result(payload, settings, "trial period ended"), slots

    # [T1] Threads that aren't the agent's to answer get a fixed status reply.
    if state is not None and state.status == "escalated":
        return guardrails.escalated_thread_result(payload, settings), slots
    if state is not None and state.status == "awaiting_approval":
        pending = await _pending_approval(state)
        if pending:
            return guardrails.awaiting_approval_result(
                payload, settings, UUID(pending["approval_id"]), pending["item_display_name"]), slots

    follow_up = state is not None and state.is_follow_up

    # 1. Embed once; it drives outage matching and SOP search.
    prefix = conversation.retrieval_text(state, payload) if follow_up else ""
    text_query = f"{prefix}\n{rag.build_query_text(payload, None)}".strip()
    embedding: list[float] | None
    try:
        embedding = await rag.embed_query(text_query)
    except Exception:  # noqa: BLE001
        logger.exception("embedding failed ticket=%s", payload.ticket_id)
        embedding = None

    # 2. Outage burst detection: new conversations only (a reply like "Windows 11"
    #    must never look like a burst), before any SOP lookup or model call.
    if embedding is not None and not follow_up:
        try:
            burst = await outage.record_and_detect(payload, embedding, summary=payload.subject)
        except Exception:  # noqa: BLE001
            logger.exception("outage detection failed ticket=%s", payload.ticket_id)
        else:
            if burst.active and burst.incident_id is not None:
                return guardrails.outage_result(
                    payload, settings, burst.incident_id, burst.category, burst.matched_tickets), slots

    # 3. Screenshots: exact error codes and dialog text, before retrieval.
    try:
        screenshots = await vision.analyze_attachments(payload, ledger)
    except (llm_client.BudgetExceededError, llm_client.BudgetUnavailableError) as exc:
        return guardrails.unavailable_result(payload, settings, _budget_reason(exc)), slots
    if screenshots and screenshots.extractions:
        try:
            embedding = await rag.embed_query(f"{prefix}\n{rag.build_query_text(payload, screenshots)}".strip())
        except Exception:  # noqa: BLE001
            logger.exception("re-embedding with screenshot findings failed ticket=%s", payload.ticket_id)

    # 4. [T1] Live identity/MDM context -> deterministic OS filter.
    user_ctx: UserContext | None = await context_enrichment.enrich(
        payload.tenant_id, payload.reporter_email, screenshots)
    platform = user_ctx.sop_platform_filter if user_ctx is not None else None
    os_filter = platform.value if platform is not None else None

    # 5. Grounded context from the tenant's own SOPs.
    sops: list[rag.SopChunk] = []
    if embedding is not None:
        try:
            sops = await rag.retrieve_relevant_sops(
                payload.tenant_id, top_k=settings.rag_top_k, os_platform=os_filter, embedding=embedding)
        except Exception:  # noqa: BLE001
            logger.exception("sop retrieval failed ticket=%s", payload.ticket_id)

    # 6. Model routing. Every call is budget-gated inside llm_client.
    try:
        decision = await llm_router.route_ticket(
            payload, sops, ledger=ledger, vision=screenshots, user_context=user_ctx,
            conversation=conversation.render(state) if state is not None else None,
        )
    except (llm_client.BudgetExceededError, llm_client.BudgetUnavailableError) as exc:
        return guardrails.unavailable_result(payload, settings, _budget_reason(exc)), slots
    except llm_router.LLMUnavailableError as exc:
        logger.error("llm unavailable ticket=%s err=%s", payload.ticket_id, exc)
        return guardrails.unavailable_result(payload, settings, "language model unavailable"), slots

    draft = decision.output
    if state is not None:
        slots = conversation.merge_facts(state, draft.known_facts)
    retrieved = {s.doc_id for s in sops}
    clarifications_used = state.clarification_count if state else 0
    approval_id = None
    item_name: str | None = None

    if draft.scope is ScopeVerdict.IN_SCOPE:
        # 7. [T1] Clarifying question: spend one DB-capped slot, or escalate at the cap.
        if draft.action is TriageAction.ASK_CLARIFICATION and state is not None:
            count = await conversation.spend_clarification(state)
            if count is None:
                draft = _with(draft, action=TriageAction.ESCALATE_TIER_2,
                              note="Clarifying-question cap reached; escalating.")
            else:
                clarifications_used = count

        # 8. [T1] Software/hardware requests go through the catalog.
        wants_item = draft.action is TriageAction.AWAIT_MANAGER_APPROVAL or (
            draft.category is TicketCategory.SOFTWARE_ACCESS and draft.requested_item.strip()
            and draft.action is not TriageAction.ASK_CLARIFICATION)
        if wants_item and draft.category is not TicketCategory.SECURITY_INCIDENT:
            if settings.feature_enabled(Feature.MANAGER_APPROVALS) and draft.requested_item.strip():
                plan = await approvals.plan_request(payload, draft.requested_item, user_ctx, payload.body)
                draft = _with(draft, action=plan.action, note=plan.note)
                if plan.action is TriageAction.AWAIT_MANAGER_APPROVAL and plan.item:
                    approval_id, item_name = plan.approval_id, plan.item.display_name
                elif plan.action is TriageAction.RESOLVE and plan.item and plan.cite_doc_id:
                    retrieved.add(plan.cite_doc_id)  # verified to exist in sop_chunks
                    draft = draft.model_copy(update={
                        "cited_sop_ids": [plan.cite_doc_id], "requires_tier2_escalation": False,
                        "draft_user_reply": guardrails.provisioning_message(
                            settings, payload.ticket_id, plan.item.display_name)})
            elif draft.action is TriageAction.AWAIT_MANAGER_APPROVAL:
                draft = _with(draft, action=TriageAction.ESCALATE_TIER_2, note="Approvals not enabled at this tier.")

    # 9. Deterministic guardrails (the result validator enforces guardrail #2).
    result = guardrails.enforce_policy(
        payload, draft, retrieved, settings, decision.model,
        clarifications_used=clarifications_used, approval_id=approval_id, approval_item_name=item_name,
    )
    return result, slots


def _with(draft: LLMTriageOutput, *, action: TriageAction, note: str) -> LLMTriageOutput:
    notes = "\n".join(filter(None, [draft.internal_tech_notes, note]))
    return draft.model_copy(update={"action": action, "internal_tech_notes": notes})


def _budget_reason(exc: Exception) -> str:
    if isinstance(exc, llm_client.BudgetExceededError):
        return "monthly token budget reached"
    return "budget ledger unavailable, so the spend cap couldn't be verified"
