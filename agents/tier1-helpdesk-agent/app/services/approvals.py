"""Tier 1: interactive RBAC manager approvals via n8n.

When a requester asks for software or hardware, the item is matched against
the tenant's `app_catalog`. That structured table, not the model, decides:

  not in catalog                  -> escalate (a human decides)
  in catalog, no approval needed  -> dispatch provisioning to n8n and resolve,
                                     citing the item's SOP (required, else escalate)
  approval required               -> create a single-use approval record, POST a
                                     signed card request to n8n, and wait

The card renders in Slack/Teams with [Approve] / [Deny]. n8n calls
POST /v1/callbacks/approval, signed. `decide_approval` (SQL) accepts a
decision once, only from the designated approver, only with the nonce, and
before expiry. On approval, provisioning is dispatched to n8n
(Entra ID / Okta group grant).

Exemption controls for guardrail #2: the wait is backed by a real record. If
the card can't be delivered, the request is cancelled and escalated. If it
expires unanswered, housekeeping escalates the thread.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.config import get_settings
from app.schemas.ticket import (
    ApprovalCallback,
    ApprovalCardRequest,
    ApprovalOutcome,
    ApprovalStatus,
    CatalogItem,
    CatalogItemType,
    IncomingTicketWebhook,
    TriageAction,
    UserContext,
)
from app.services import conversation, notifier, supabase_client

logger = logging.getLogger(__name__)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


async def load_catalog(tenant_id: str) -> list[CatalogItem]:
    rows = await supabase_client.select(
        "app_catalog", {"tenant_id": supabase_client.eq(tenant_id), "active": "eq.true"}, limit=1000
    )
    return [CatalogItem.model_validate({k: v for k, v in r.items() if k in CatalogItem.model_fields}) for r in rows]


def match_catalog(requested: str, catalog: list[CatalogItem]) -> CatalogItem | None:
    """Deterministic match on key, display name or alias (case/punctuation-insensitive).

    Exact normalised match wins. Otherwise a unique item whose name is
    contained in the request ("adobe acrobat pro please" -> acrobat-pro). If
    two items could match, return None and let a human decide.
    """
    wanted = _norm(requested)
    if not wanted:
        return None
    names = {item.item_key: {_norm(n) for n in [item.item_key, item.display_name, *item.aliases] if _norm(n)}
             for item in catalog}
    exact = [i for i in catalog if wanted in names[i.item_key]]
    if len(exact) == 1:
        return exact[0]
    contained = [
        i for i in catalog
        if any(len(n) >= 4 and (n in wanted or (len(wanted) >= 4 and wanted in n)) for n in names[i.item_key])
    ]
    return contained[0] if len(contained) == 1 else None


def resolve_approver(item: CatalogItem, user: UserContext | None) -> str | None:
    if item.approver_role == "direct_manager":
        return user.manager_email if user else None
    if item.approver_role == "app_owner":
        return item.app_owner_email
    return get_settings().it_manager_email.strip().lower() or None


@dataclass
class ApprovalPlan:
    """What the pipeline should do with a requested item."""

    action: TriageAction
    item: CatalogItem | None = None
    approval_id: UUID | None = None
    cite_doc_id: str | None = None
    note: str = ""


async def _sop_exists(tenant_id: str, doc_id: str) -> bool:
    rows = await supabase_client.select(
        "sop_chunks", {"tenant_id": supabase_client.eq(tenant_id), "doc_id": supabase_client.eq(doc_id)},
        columns="doc_id", limit=1,
    )
    return bool(rows)


async def plan_request(
    payload: IncomingTicketWebhook, requested_item: str, user: UserContext | None, justification: str
) -> ApprovalPlan:
    """Decide and (where needed) start the approval for a requested item. Never raises."""
    settings = get_settings()
    try:
        item = match_catalog(requested_item, await load_catalog(payload.tenant_id))
        if item is None:
            return ApprovalPlan(TriageAction.ESCALATE_TIER_2, note=f"'{requested_item}' not matched in catalog")

        if not item.requires_manager_approval:
            if not item.doc_id or not await _sop_exists(payload.tenant_id, item.doc_id):
                return ApprovalPlan(TriageAction.ESCALATE_TIER_2, item,
                                    note=f"{item.item_key}: no approval needed, but no SOP on record to cite")
            dispatched = await dispatch_provisioning(payload.tenant_id, item, payload.reporter_email,
                                                     payload.ticket_id, approval_id=None)
            if not dispatched:
                return ApprovalPlan(TriageAction.ESCALATE_TIER_2, item, note="provisioning dispatch failed")
            return ApprovalPlan(TriageAction.RESOLVE, item, cite_doc_id=item.doc_id,
                                note=f"{item.item_key}: auto-provisioned (no approval required)")

        approver = resolve_approver(item, user)
        if not approver:
            return ApprovalPlan(TriageAction.ESCALATE_TIER_2, item,
                                note=f"{item.item_key}: no {item.approver_role} on record to approve")
        if approver == payload.reporter_email:
            return ApprovalPlan(TriageAction.ESCALATE_TIER_2, item, note=f"{item.item_key}: requester can't self-approve")

        thread_id = payload.conversation_key
        await conversation.ensure_thread(payload, thread_id)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.approval_ttl_hours)
        row = await supabase_client.insert_returning(
            "approval_requests",
            {
                "tenant_id": payload.tenant_id, "thread_id": thread_id, "ticket_id": payload.ticket_id,
                "requester_email": payload.reporter_email, "approver_email": approver,
                "item_type": item.item_type.value, "item_key": item.item_key,
                "item_display_name": item.display_name, "justification": justification[:2000] or None,
                "expires_at": expires_at.isoformat(),
            },
        )
        approval_id = UUID(row["approval_id"])
        card = ApprovalCardRequest(
            approval_id=approval_id,
            callback_nonce=row["callback_nonce"],
            tenant_id=payload.tenant_id,
            ticket_id=payload.ticket_id,
            thread_id=thread_id,
            requester_email=payload.reporter_email,
            approver_email=approver,
            item_type=item.item_type,
            item_key=item.item_key,
            item_display_name=item.display_name,
            justification=justification[:2000] or None,
            license_cost_usd=item.license_cost_usd,
            expires_at=expires_at,
            callback_url=f"{str(settings.public_base_url).rstrip('/')}/v1/callbacks/approval",
            source_system=payload.source_system,
        )
        if not await notifier.post_signed(str(settings.n8n_approval_webhook_url), card.model_dump(mode="json")):
            # The card never reached the approver, so the wait isn't covered: cancel and escalate.
            await supabase_client.update(
                "approval_requests",
                {"tenant_id": supabase_client.eq(payload.tenant_id), "approval_id": supabase_client.eq(approval_id)},
                {"status": ApprovalStatus.CANCELLED.value},
            )
            return ApprovalPlan(TriageAction.ESCALATE_TIER_2, item, note="approval card delivery failed")
        await conversation.set_status(payload.tenant_id, thread_id, "awaiting_approval",
                                      TriageAction.AWAIT_MANAGER_APPROVAL)
        return ApprovalPlan(TriageAction.AWAIT_MANAGER_APPROVAL, item, approval_id=approval_id,
                            note=f"approval {approval_id} sent to {approver}")
    except Exception:  # noqa: BLE001
        logger.exception("approval planning failed ticket=%s", payload.ticket_id)
        return ApprovalPlan(TriageAction.ESCALATE_TIER_2, note="approval service error")


async def dispatch_provisioning(
    tenant_id: str, item: CatalogItem, requester_email: str, ticket_id: str, approval_id: UUID | None
) -> bool:
    settings = get_settings()
    return await notifier.post_signed(
        str(settings.n8n_approval_webhook_url),
        {
            "event": "provisioning.requested",
            "tenant_id": tenant_id,
            "ticket_id": ticket_id,
            "approval_id": str(approval_id) if approval_id else None,
            "requester_email": requester_email,
            "item_type": item.item_type.value,
            "item_key": item.item_key,
            "item_display_name": item.display_name,
            "provisioning_group": item.provisioning_group,
        },
    )


async def handle_callback(callback: ApprovalCallback) -> ApprovalOutcome | None:
    """Apply an approver's decision. None means it was not accepted (see decide_approval)."""
    rows = await supabase_client.rpc(
        "decide_approval",
        {
            "p_tenant_id": callback.tenant_id,
            "p_approval_id": str(callback.approval_id),
            "p_nonce": callback.callback_nonce,
            "p_decision": callback.decision,
            "p_decided_by": callback.decided_by_email,
            "p_note": callback.decision_note,
        },
    )
    if not rows:
        logger.warning("approval decision rejected approval=%s by=%s", callback.approval_id, callback.decided_by_email)
        return None
    row = rows[0]
    settings = get_settings()

    if callback.decision == "denied":
        await conversation.set_status(callback.tenant_id, row["thread_id"], "resolved", TriageAction.RESOLVE)
        await notifier.post_signed(str(settings.n8n_approval_webhook_url), {
            "event": "approval.denied", "tenant_id": callback.tenant_id, "approval_id": str(callback.approval_id),
            "ticket_id": row["ticket_id"], "requester_email": row["requester_email"],
            "item_display_name": row["item_display_name"], "decision_note": callback.decision_note,
        })
        return ApprovalOutcome(approval_id=callback.approval_id, status=ApprovalStatus.DENIED,
                               provisioning_dispatched=False, message="Request denied; requester notified.")

    item = CatalogItem(
        tenant_id=callback.tenant_id, item_key=row["item_key"], item_type=CatalogItemType(row["item_type"]),
        display_name=row["item_display_name"],
    )
    catalog = {i.item_key: i for i in await load_catalog(callback.tenant_id)}
    item = catalog.get(row["item_key"], item)
    dispatched = await dispatch_provisioning(callback.tenant_id, item, row["requester_email"], row["ticket_id"],
                                             callback.approval_id)
    if dispatched:
        await conversation.set_status(callback.tenant_id, row["thread_id"], "resolved", TriageAction.RESOLVE)
        message = "Approved; provisioning dispatched."
    else:
        # Approved but nothing will happen automatically: a technician must finish it.
        await conversation.set_status(callback.tenant_id, row["thread_id"], "escalated",
                                      TriageAction.ESCALATE_TIER_2, reason="provisioning_dispatch_failed")
        message = "Approved, but provisioning dispatch failed; escalated to Tier 2."
    return ApprovalOutcome(approval_id=callback.approval_id, status=ApprovalStatus.APPROVED,
                           provisioning_dispatched=dispatched, message=message)
