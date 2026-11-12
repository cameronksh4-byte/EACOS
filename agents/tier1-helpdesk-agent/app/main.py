"""FastAPI entrypoint and routes.

Tier 0 (always available, kill-switchable):
    GET  /health
    POST /v1/triage                               x-webhook-secret
    GET  /v1/telemetry/{tenant_id}/roi-summary    x-webhook-secret, ROI_TELEMETRY
    POST /webhooks/slack | /webhooks/teams | /webhooks/jira   platform signatures

Tier 1 and Tier 2 routes are added in Phase 3 behind `require_feature`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.config import Feature, get_settings
from app.gating import require_feature, require_tenant, require_webhook_secret
from app.schemas.ticket import AgentTriageResult, IncomingTicketWebhook, ROISummary
from app.services import (
    background,
    llm_client,
    notifier,
    outage,
    rag,
    supabase_client,
    telemetry,
    ticketing,
    vision,
)
from app.services.pipeline import process_ticket

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("tier1_agent")

TEAMS_SYNC_REPLY_SECONDS = 4.5  # Teams outgoing webhooks time out at 5s
NOTIFICATION_RETRY_SECONDS = 60


async def _notification_retry_loop() -> None:
    """Keep retrying owed Tier 2 notifications (guardrail #2 exemption controls)."""
    while True:
        await asyncio.sleep(NOTIFICATION_RETRY_SECONDS)
        if get_settings().feature_enabled(Feature.OUTAGE_DETECTION):
            try:
                delivered = await outage.retry_pending_notifications()
                if delivered:
                    logger.info("delivered %d pending outage notifications", delivered)
            except Exception:  # noqa: BLE001
                logger.exception("outage notification retry failed")


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
                  vision.aclose, notifier.aclose):
        await close()


app = FastAPI(title="Tier 1 IT Help Desk Agent", version="2.0.0", lifespan=lifespan)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "tier": get_settings().service_tier.value}


@app.post(
    "/v1/triage",
    response_model=AgentTriageResult,
    dependencies=[Depends(require_webhook_secret), Depends(require_feature(Feature.TRIAGE_AND_DRAFT))],
)
async def triage_ticket(payload: IncomingTicketWebhook):
    require_tenant(payload.tenant_id)
    return await process_ticket(payload)


@app.get(
    "/v1/telemetry/{tenant_id}/roi-summary",
    response_model=ROISummary,
    dependencies=[Depends(require_webhook_secret), Depends(require_feature(Feature.ROI_TELEMETRY))],
)
async def roi_summary(tenant_id: str, days: int = Query(30, ge=1, le=365)):
    require_tenant(tenant_id)
    try:
        return await telemetry.roi_summary(tenant_id, days=days)
    except Exception as exc:  # noqa: BLE001
        logger.exception("roi summary failed tenant=%s", tenant_id)
        raise HTTPException(status_code=503, detail="telemetry store unavailable") from exc


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

    data = json.loads(body)
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}
    # Slack needs a 2xx within 3s; it retries otherwise. Ack fast, work later.
    if request.headers.get("x-slack-retry-num"):
        return {"ok": True}
    if data.get("type") == "event_callback":
        event_id = data.get("event_id", "")
        if event_id and ticketing.seen_events.check_and_add(f"slack:{event_id}"):
            return {"ok": True}
        background.spawn(ticketing.handle_slack_event(data.get("event", {})), f"slack {event_id}")
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

    ticket = ticketing.teams_activity_to_ticket(json.loads(body))
    task = asyncio.create_task(process_ticket(ticket))
    done, _ = await asyncio.wait({task}, timeout=TEAMS_SYNC_REPLY_SECONDS)
    if task in done and task.exception() is None:
        return JSONResponse({"type": "message", "text": task.result().draft_user_reply})

    async def follow_up() -> None:
        result = await task
        await ticketing.post_teams_followup(result.draft_user_reply)

    background.spawn(follow_up(), f"teams {ticket.ticket_id}")
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

    data = json.loads(body)
    if data.get("webhookEvent") != "jira:issue_created" or "issue" not in data:
        return {"ok": True, "ignored": True}
    key = data["issue"].get("key", "")
    if ticketing.seen_events.check_and_add(f"jira:{key}"):
        return {"ok": True}
    background.spawn(ticketing.handle_jira_issue(data["issue"]), f"jira {key}")
    return {"ok": True}
