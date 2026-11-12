"""FastAPI entrypoint and webhook routes."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.schemas.ticket import AgentTriageResult, IncomingTicketWebhook
from app.services import llm_router, rag, supabase_client, ticketing
from app.services.pipeline import process_ticket

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("tier1_agent")

TEAMS_SYNC_REPLY_SECONDS = 4.5  # Teams outgoing webhooks time out at 5s
_background: set[asyncio.Task[Any]] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    missing = get_settings().missing_core_settings()
    if missing:
        raise RuntimeError(f"missing required settings: {', '.join(missing)}")
    yield
    for close in (llm_router.aclose, rag.aclose, supabase_client.aclose, ticketing.aclose):
        await close()


app = FastAPI(title="Tier 1 IT Help Desk Agent", version="1.0.0", lifespan=lifespan)


def _require_secret(provided: str) -> None:
    expected = get_settings().webhook_secret.get_secret_value()
    if not expected or not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def _require_tenant(tenant_id: str) -> None:
    if tenant_id != get_settings().tenant_id:
        raise HTTPException(status_code=403, detail="tenant not served by this deployment")


def _spawn(coro: Any, label: str) -> None:
    """Run work after the HTTP response, logging (not losing) failures."""

    async def runner() -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001
            logger.exception("background task failed: %s", label)

    task = asyncio.create_task(runner())
    _background.add(task)
    task.add_done_callback(_background.discard)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/v1/triage", response_model=AgentTriageResult)
async def triage_ticket(payload: IncomingTicketWebhook, x_webhook_secret: str = Header(...)):
    _require_secret(x_webhook_secret)
    _require_tenant(payload.tenant_id)
    # RAG -> Haiku/Sonnet routing -> deterministic guardrails (scope declines,
    # citation checks, forced escalation for security / low confidence).
    return await process_ticket(payload)


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
        _spawn(ticketing.handle_slack_event(data.get("event", {})), f"slack {event_id}")
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

    _spawn(follow_up(), f"teams {ticket.ticket_id}")
    return JSONResponse(
        {"type": "message", "text": "Thanks! I'm looking into this and will reply here in a moment."}
    )


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
    _spawn(ticketing.handle_jira_issue(data["issue"]), f"jira {key}")
    return {"ok": True}
