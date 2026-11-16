"""FastAPI entrypoint and routes.

Tier 0 (always available, kill-switchable):
    GET  /health
    POST /v1/triage                               x-webhook-secret
    GET  /v1/telemetry/{tenant_id}/roi-summary    x-webhook-secret, ROI_TELEMETRY
    POST /webhooks/slack | /webhooks/teams | /webhooks/jira   platform signatures

Tier 1 (MANAGER_APPROVALS):
    POST /v1/callbacks/approval                   n8n signature

Tier 2 (KNOWLEDGE_FLYWHEEL, EXECUTIVE_REPORTS):
    POST /v1/webhooks/ticket-resolved             x-webhook-secret
    POST /v1/callbacks/sop-draft                  n8n signature
    POST /v1/reports/weekly                       x-webhook-secret (called by an n8n schedule)

Public website chat (WEBCHAT_ENABLED; signed by the website's server):
    POST /v1/webchat/message                      one metered question (5 free, then sign-up)
    POST /v1/webchat/grants                       plan change / credit purchase (idempotent)
    POST /v1/webchat/account                      plan, usage and credits for an account

Routes above the deployment's tier return 404.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import Feature, get_settings
from app.gating import (
    require_feature,
    require_n8n_signature,
    require_tenant,
    require_webchat_enabled,
    require_webchat_signature,
    require_webhook_secret,
)
from app.schemas.ticket import (
    AgentTriageResult,
    ApprovalCallback,
    ApprovalOutcome,
    IncomingTicketWebhook,
    ROISummary,
    SOPDraftDecision,
    TicketResolvedWebhook,
    WebChatAccount,
    WebChatAccountQuery,
    WebChatGrant,
    WebChatRequest,
    WebChatResponse,
    WeeklyReport,
)
from app.services import (
    approvals,
    background,
    context_enrichment,
    conversation,
    flywheel,
    llm_client,
    notifier,
    outage,
    rag,
    reports,
    supabase_client,
    telemetry,
    ticketing,
    vision,
    webchat,
)
from app.services.pipeline import process_ticket

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("tier1_agent")

TEAMS_SYNC_REPLY_SECONDS = 4.5  # Teams outgoing webhooks time out at 5s
NOTIFICATION_RETRY_SECONDS = 60
HOUSEKEEPING_EVERY_N_LOOPS = 5


async def maintenance_tick(run_housekeeping: bool) -> None:
    """One pass: DB housekeeping, then any owed Tier 2 notifications.

    Housekeeping expires unanswered approvals and escalates their threads, so
    pg_cron isn't required. It's idempotent, so several tasks running it is fine.
    """
    settings = get_settings()
    if run_housekeeping:
        try:
            await supabase_client.rpc("run_housekeeping", {})
        except Exception:  # noqa: BLE001
            logger.exception("housekeeping failed")
    if settings.feature_enabled(Feature.OUTAGE_DETECTION):
        try:
            if delivered := await outage.retry_pending_notifications():
                logger.info("delivered %d pending outage notifications", delivered)
        except Exception:  # noqa: BLE001
            logger.exception("outage notification retry failed")
    if settings.feature_enabled(Feature.KNOWLEDGE_FLYWHEEL):
        try:
            if delivered := await flywheel.retry_pending_reviews():
                logger.info("delivered %d pending SOP-draft reviews", delivered)
        except Exception:  # noqa: BLE001
            logger.exception("SOP-draft review retry failed")
    if settings.feature_enabled(Feature.MULTI_TURN) or settings.feature_enabled(Feature.MANAGER_APPROVALS):
        try:
            if delivered := await conversation.retry_pending_escalations():
                logger.info("delivered %d pending thread escalations", delivered)
        except Exception:  # noqa: BLE001
            logger.exception("escalation notification retry failed")


async def _notification_retry_loop() -> None:
    """Keep retrying owed Tier 2 notifications (guardrail #2 exemption controls)."""
    loops = 0
    while True:
        await asyncio.sleep(NOTIFICATION_RETRY_SECONDS)
        loops += 1
        # Every worker of every task runs this loop; one of them takes each tick.
        try:
            mine = await supabase_client.rpc(
                "try_acquire_maintenance_lease",
                {"p_name": "maintenance", "p_ttl_seconds": NOTIFICATION_RETRY_SECONDS - 10})
        except Exception as exc:  # noqa: BLE001
            logger.warning("maintenance lease unavailable err=%s; skipping this tick", type(exc).__name__)
            continue
        if not mine:
            continue
        await maintenance_tick(run_housekeeping=loops % HOUSEKEEPING_EVERY_N_LOOPS == 0)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    missing = settings.missing_core_settings()
    if missing:
        raise RuntimeError(f"missing required settings: {', '.join(missing)}")
    logger.info(
        "starting tier=%s features=%s tenants=%s provider=%s",
        settings.service_tier.value, [f.value for f in settings.enabled_features()],
        settings.allowed_tenant_ids, settings.llm_provider.value,
    )
    retry_task = asyncio.create_task(_notification_retry_loop(), name="notification-retry")
    yield
    retry_task.cancel()
    with suppress(asyncio.CancelledError):
        await retry_task
    await background.drain()
    for close in (llm_client.aclose, rag.aclose, supabase_client.aclose, ticketing.aclose,
                  vision.aclose, notifier.aclose, context_enrichment.aclose):
        await close()


# No interactive docs or schema in production: they would list every route,
# including the ones this deployment's tier returns 404 for.
app = FastAPI(title="Tier 1 IT Help Desk Agent", version="3.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "tier": get_settings().service_tier.value}


@app.post(
    "/v1/triage",
    response_model=AgentTriageResult,
    dependencies=[Depends(require_feature(Feature.TRIAGE_AND_DRAFT)), Depends(require_webhook_secret)],
)
async def triage_ticket(payload: IncomingTicketWebhook):
    require_tenant(payload.tenant_id)
    return await process_ticket(payload)


@app.get(
    "/v1/telemetry/{tenant_id}/roi-summary",
    response_model=ROISummary,
    dependencies=[Depends(require_feature(Feature.ROI_TELEMETRY)), Depends(require_webhook_secret)],
)
async def roi_summary(tenant_id: str, days: int = Query(30, ge=1, le=365)):
    require_tenant(tenant_id)
    try:
        return await telemetry.roi_summary(tenant_id, days=days)
    except Exception as exc:  # noqa: BLE001
        logger.exception("roi summary failed tenant=%s", tenant_id)
        raise HTTPException(status_code=503, detail="telemetry store unavailable") from exc


def _json_object(body: bytes) -> dict:
    """A signed body that isn't a JSON object is a bad request, never a 500."""
    try:
        data = json.loads(body)
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        raise HTTPException(status_code=400, detail="body is not valid JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    return data


def _parse(model, body: bytes):
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False)) from exc


# --- Tier 1: approvals -----------------------------------------------------------


@app.post(
    "/v1/callbacks/approval",
    response_model=ApprovalOutcome,
    dependencies=[Depends(require_feature(Feature.MANAGER_APPROVALS))],
)
async def approval_callback(body: bytes = Depends(require_n8n_signature)):
    callback: ApprovalCallback = _parse(ApprovalCallback, body)
    require_tenant(callback.tenant_id)
    outcome = await approvals.handle_callback(callback)
    if outcome is None:
        raise HTTPException(status_code=409, detail="approval not pending, expired, or not yours to decide")
    return outcome


# --- Tier 2: flywheel and reports ------------------------------------------------


@app.post(
    "/v1/webhooks/ticket-resolved",
    status_code=202,
    dependencies=[Depends(require_feature(Feature.KNOWLEDGE_FLYWHEEL)), Depends(require_webhook_secret)],
)
async def ticket_resolved(event: TicketResolvedWebhook):
    require_tenant(event.tenant_id)
    # Synthesis can take a while; acknowledge now, notify the IT manager when done.
    background.spawn(flywheel.process_resolved_ticket(event), f"flywheel {event.ticket_id}")
    return {"accepted": True, "ticket_id": event.ticket_id}


@app.post(
    "/v1/callbacks/sop-draft",
    dependencies=[Depends(require_feature(Feature.KNOWLEDGE_FLYWHEEL))],
)
async def sop_draft_callback(body: bytes = Depends(require_n8n_signature)):
    decision: SOPDraftDecision = _parse(SOPDraftDecision, body)
    require_tenant(decision.tenant_id)
    outcome = await flywheel.apply_review(decision)
    if outcome is None:
        raise HTTPException(status_code=409, detail="draft not pending review")
    return outcome


@app.post(
    "/v1/reports/weekly",
    response_model=WeeklyReport,
    dependencies=[Depends(require_feature(Feature.EXECUTIVE_REPORTS)), Depends(require_webhook_secret)],
)
async def weekly_report(tenant_id: str = Query(..., pattern=r"^[a-z0-9-]{2,64}$"), deliver: bool = True):
    require_tenant(tenant_id)
    return await reports.weekly_report(tenant_id, deliver=deliver)


# --- Public website chat (free trial) --------------------------------------------


@app.post("/v1/webchat/message", response_model=WebChatResponse,
          dependencies=[Depends(require_webchat_enabled)])
async def webchat_message(body: bytes = Depends(require_webchat_signature)):
    request: WebChatRequest = _parse(WebChatRequest, body)
    try:
        return await webchat.handle_message(request)
    except webchat.InvalidRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/webchat/grants", dependencies=[Depends(require_webchat_enabled)])
async def webchat_grant(body: bytes = Depends(require_webchat_signature)):
    """Called by the website's payment handler: set a plan and/or add purchased credits."""
    grant: WebChatGrant = _parse(WebChatGrant, body)
    try:
        return await webchat.apply_grant(grant)
    except webchat.InvalidRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/webchat/account", response_model=WebChatAccount,
          dependencies=[Depends(require_webchat_enabled)])
async def webchat_account(body: bytes = Depends(require_webchat_signature)):
    query: WebChatAccountQuery = _parse(WebChatAccountQuery, body)
    found = await webchat.account(query.email)
    if found is None:
        raise HTTPException(status_code=404, detail="no such account")
    return found


# --- Chat integrations -----------------------------------------------------------


@app.post("/webhooks/slack")
async def slack_events(request: Request):
    body = await request.body()
    settings = get_settings()
    if not ticketing.verify_slack_signature(
        settings.slack_signing_secret.get_secret_value(),
        request.headers.get("x-slack-request-timestamp", ""),
        body,
        request.headers.get("x-slack-signature", ""),
    ):
        raise HTTPException(status_code=401, detail="invalid slack signature")

    data = _json_object(body)
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}
    # Slack needs a 2xx within 3s; it retries otherwise. Ack fast, work later.
    if request.headers.get("x-slack-retry-num"):
        return {"ok": True}
    if data.get("type") == "event_callback":
        event_id = str(data.get("event_id") or "")
        event = data.get("event")
        if not isinstance(event, dict):
            return {"ok": True}
        if event_id and ticketing.seen_events.check_and_add(f"slack:{event_id}"):
            return {"ok": True}
        auths = data.get("authorizations")
        bot_user_id = ""
        if isinstance(auths, list) and auths and isinstance(auths[0], dict):
            bot_user_id = str(auths[0].get("user_id") or "")
        background.spawn(ticketing.handle_slack_event(event, bot_user_id), f"slack {event_id}")
    return {"ok": True}


@app.post("/webhooks/teams")
async def teams_outgoing_webhook(request: Request):
    body = await request.body()
    settings = get_settings()
    if not ticketing.verify_teams_signature(
        settings.teams_outgoing_webhook_secret.get_secret_value(),
        body,
        request.headers.get("authorization", ""),
    ):
        raise HTTPException(status_code=401, detail="invalid teams signature")

    activity = _json_object(body)
    task = asyncio.create_task(ticketing.process_teams_activity(activity))
    done, _ = await asyncio.wait({task}, timeout=TEAMS_SYNC_REPLY_SECONDS)
    if task in done and task.exception() is None:
        return JSONResponse({"type": "message", "text": task.result().draft_user_reply})

    async def follow_up() -> None:
        result = await task
        await ticketing.post_teams_followup(result.draft_user_reply)

    background.spawn(follow_up(), f"teams {str(activity.get('id') or '')[:60]}")
    return JSONResponse({"type": "message", "text": "Thanks! I'm looking into this and will reply here in a moment."})


@app.post("/webhooks/jira")
async def jira_webhook(request: Request):
    body = await request.body()
    settings = get_settings()
    if not ticketing.verify_jira_signature(
        settings.jira_webhook_secret.get_secret_value(),
        body,
        request.headers.get("x-hub-signature", ""),
    ):
        raise HTTPException(status_code=401, detail="invalid jira signature")

    data = _json_object(body)
    issue = data.get("issue")
    if data.get("webhookEvent") != "jira:issue_created" or not isinstance(issue, dict):
        return {"ok": True, "ignored": True}
    key = issue.get("key")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", key):
        return {"ok": True, "ignored": True}
    if ticketing.seen_events.check_and_add(f"jira:{key}"):
        return {"ok": True}
    background.spawn(ticketing.handle_jira_issue(data["issue"]), f"jira {key}")
    return {"ok": True}
