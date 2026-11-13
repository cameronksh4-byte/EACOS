"""In-memory stand-ins for Supabase, Claude, Voyage and n8n (no network, no spend).

FakeSupabase mimics PostgREST filters (eq./is.null/gte., order, limit) over
plain dict tables. Its RPCs mirror the SQL functions' semantics. Those
functions are tested for real against Postgres in supabase/tests/.
"""

from __future__ import annotations

import copy
import json
import secrets
import uuid
from datetime import datetime, timezone

import httpx
import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from app.services import llm_client, notifier, rag, supabase_client

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _match(row: dict, filters: dict[str, str]) -> bool:
    for key, cond in filters.items():
        if key in ("order", "select", "limit"):
            continue
        value = row.get(key)
        op, _, arg = str(cond).partition(".")
        if op == "eq":
            actual = str(value).lower() if isinstance(value, bool) else str(value)
            if actual != arg:
                return False
        elif op == "is" and arg == "null":
            if value is not None:
                return False
        elif op == "gte":
            if value is None or str(value) < arg:
                return False
        else:
            raise AssertionError(f"unsupported filter {key}={cond}")
    return True


class FakeSupabase:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.cap_ok = True
        self.ledger_down = False
        self.reservations: list[dict] = []
        self.settled: list[dict] = []
        self.sops: list[dict] = []
        self.burst_rows: list[dict] = []
        self.incident = {"incident_id": str(uuid.uuid4()), "is_new": True, "needs_tier2_notification": True}
        self.marked: list[str] = []
        self.rpc_calls: list[tuple[str, dict]] = []
        self.roi = {"unique_tickets": 40, "total_triages": 52, "deflected_tickets": 22,
                    "deflection_rate_pct": 55.0, "hours_saved": 5.5, "token_cost_usd": 0.31, "outage_tickets": 6}

    # -- convenience views used by tests ------------------------------------
    def t(self, name: str) -> list[dict]:
        return self.tables.setdefault(name, [])

    @property
    def telemetry(self) -> list[dict]:
        return self.t("ticket_telemetry")

    @property
    def recent(self) -> dict[str, dict]:
        return {r["ticket_id"]: r for r in self.t("recent_tickets")}

    # -- PostgREST -------------------------------------------------------------
    def _defaults(self, table: str, row: dict) -> dict:
        row = dict(row)
        now = datetime.now(timezone.utc).isoformat()
        if table == "approval_requests":
            row.setdefault("approval_id", str(uuid.uuid4()))
            row.setdefault("callback_nonce", secrets.token_hex(24))
            row.setdefault("status", "pending")
        if table == "sop_drafts":
            row.setdefault("draft_id", str(uuid.uuid4()))
            row.setdefault("status", "draft_pending_review")
        if table == "conversation_threads":
            row.setdefault("status", "active")
            row.setdefault("clarification_count", 0)
            row.setdefault("slots", {})
            row.setdefault("escalation_notified_at", None)
        row.setdefault("created_at", now)
        return row

    async def select(self, table, filters, columns="*", limit=None):
        rows = [copy.deepcopy(r) for r in self.t(table) if _match(r, filters)]
        order = filters.get("order")
        if order:
            col, _, direction = order.partition(".")
            rows.sort(key=lambda r: r.get(col) or "", reverse=direction == "desc")
        return rows[:limit] if limit else rows

    async def insert(self, table, row):
        for r in row if isinstance(row, list) else [row]:
            self.t(table).append(self._defaults(table, r))

    async def insert_returning(self, table, row):
        full = self._defaults(table, row)
        self.t(table).append(full)
        return copy.deepcopy(full)

    def _key(self, row, on_conflict):
        return tuple(row.get(c) for c in on_conflict.split(","))

    async def insert_ignore(self, table, row, on_conflict):
        if any(self._key(r, on_conflict) == self._key(row, on_conflict) for r in self.t(table)):
            return
        self.t(table).append(self._defaults(table, row))

    async def upsert(self, table, row, on_conflict):
        for r in row if isinstance(row, list) else [row]:
            for existing in self.t(table):
                if self._key(existing, on_conflict) == self._key(r, on_conflict):
                    existing.update(r)
                    break
            else:
                self.t(table).append(self._defaults(table, r))

    async def update(self, table, filters, values):
        changed = []
        for r in self.t(table):
            if _match(r, filters):
                r.update(copy.deepcopy(values))
                changed.append(copy.deepcopy(r))
        return changed

    # -- RPCs (mirror the SQL functions) -----------------------------------------
    async def rpc(self, fn, params):
        self.rpc_calls.append((fn, params))
        if fn == "try_reserve_llm_budget":
            if self.ledger_down:
                raise httpx.ConnectError("down")
            if not self.cap_ok:
                return None
            self.reservations.append(params)
            return f"res-{len(self.reservations)}"
        if fn == "settle_llm_budget":
            self.settled.append(params)
            return True
        if fn == "match_sop_chunks":
            os_ = params["p_os_platform"]
            return [s for s in self.sops if os_ is None or s["os_platform"] in ("any", os_)]
        if fn == "match_recent_tickets":
            return self.burst_rows
        if fn == "register_outage_cluster":
            return [self.incident]
        if fn == "mark_outage_tier2_notified":
            self.marked.append(params["p_incident_id"])
            return True
        if fn == "tenant_roi_summary":
            return [self.roi]
        if fn == "tenant_knowledge_gaps":
            return [{"topic": "adobe licence", "ticket_count": 4, "last_seen_at": None}]
        if fn == "increment_clarification":
            for r in self.t("conversation_threads"):
                if r["tenant_id"] == params["p_tenant_id"] and r["thread_id"] == params["p_thread_id"]:
                    if r["clarification_count"] >= min(max(params["p_max"], 0), 2):
                        return None
                    r["clarification_count"] += 1
                    r["status"] = "awaiting_user"
                    r["last_action"] = "ASK_CLARIFICATION"
                    return r["clarification_count"]
            return None
        if fn == "decide_approval":
            for r in self.t("approval_requests"):
                if (r["tenant_id"] == params["p_tenant_id"] and r["approval_id"] == params["p_approval_id"]
                        and r["callback_nonce"] == params["p_nonce"] and r["status"] == "pending"
                        and r["approver_email"] == params["p_decided_by"].lower()):
                    r.update(status=params["p_decision"], decided_by=params["p_decided_by"].lower(),
                             decided_at=datetime.now(timezone.utc).isoformat())
                    return [copy.deepcopy(r)]
            return []
        if fn == "run_housekeeping":
            return None
        raise AssertionError(f"unexpected rpc {fn}")


def _is(body: dict, prefix: str) -> bool:
    return body["system"][0]["text"].startswith(prefix)


class FakeClaude:
    """Canned JSON per purpose (triage outputs are consumed in order)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.triage: list[dict] = [triage_output()]
        self.vision = {"screenshots": [vision_output()]}
        self.synthesis = synthesis_output()

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if _is(body, "You read screenshots"):
            payload = self.vision
        elif _is(body, "You turn a technician"):
            payload = self.synthesis
        else:
            payload = self.triage[min(len(self.triage_calls()) - 1, len(self.triage) - 1)]
        return httpx2.Response(200, json={
            "id": "msg", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 300},
        })

    def triage_calls(self):
        return [r for r in self.requests if not _is(r, "You read screenshots") and not _is(r, "You turn a technician")]

    def vision_calls(self):
        return [r for r in self.requests if _is(r, "You read screenshots")]

    def synthesis_calls(self):
        return [r for r in self.requests if _is(r, "You turn a technician")]

    def last_triage_text(self) -> str:
        return self.triage_calls()[-1]["messages"][0]["content"][0]["text"]


class FakeN8n:
    """Captures signed POSTs made through notifier.post_signed."""

    def __init__(self):
        self.posts: list[dict] = []
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.posts.append({"url": str(request.url), "body": json.loads(request.content),
                           "raw": request.content, "signature": request.headers.get("X-Agent-Signature")})
        return httpx.Response(500 if self.fail else 200, json={"ok": True})

    def events(self, name: str) -> list[dict]:
        return [p["body"] for p in self.posts if p["body"].get("event") == name]


def triage_output(**kw) -> dict:
    base = {
        "scope": "in_scope", "action": "RESOLVE", "category": "vpn_network", "severity": "P3_MEDIUM",
        "confidence_score": 0.93, "requires_tier2_escalation": False, "cited_sop_ids": ["SOP-NET-001"],
        "draft_user_reply": "1. Disconnect and reconnect GlobalProtect.", "clarifying_question": "",
        "requested_item": "", "missing_sop_topic": "", "known_facts": [], "internal_tech_notes": "",
    }
    base.update(kw)
    return base


def vision_output(**kw) -> dict:
    base = {"error_codes": ["0x80070005"], "dialog_text": "Access is denied.", "application_name": "GlobalProtect",
            "ui_state": "Connection failed dialog.", "os_platform_hint": "windows", "contains_sensitive_data": False}
    base.update(kw)
    return base


def synthesis_output(**kw) -> dict:
    base = {
        "covers_knowledge_gap": True,
        "reasoning": "Reusable fix for a common Outlook error not covered by existing SOPs.",
        "title": "Fix Outlook error 0x800CCC0E",
        "system_name": "Outlook",
        "os_platform": "macos",
        "markdown_body": "## When to use\nOutlook shows 0x800CCC0E.\n\n## Steps for the user\n"
                         "1. Quit Outlook.\n2. Remove the account and add it again.\n\n"
                         "## Escalate to Tier 2 when\nThe error returns after re-adding.",
        "redactions_made": ["hostname"],
    }
    base.update(kw)
    return base


@pytest.fixture
def env(monkeypatch):
    sb = FakeSupabase()
    for name in ("rpc", "insert", "insert_returning", "insert_ignore", "upsert", "update", "select"):
        monkeypatch.setattr(supabase_client, name, getattr(sb, name))

    claude = FakeClaude()
    client = AsyncAnthropic(api_key="test", max_retries=0,
                            http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(claude.handler)))
    monkeypatch.setattr(llm_client, "_client", client)

    embedded: list[str] = []

    async def fake_embed(text):
        embedded.append(text)
        return [0.1] * 1024

    async def fake_embed_docs(texts, batch=64):
        return [[0.2] * 1024 for _ in texts]

    monkeypatch.setattr(rag, "embed_query", fake_embed)
    monkeypatch.setattr(rag, "embed_documents", fake_embed_docs)

    notified: list[dict] = []

    n8n_up = {"value": True}

    async def fake_notify(event, tenant_id, payload, text, *, require_n8n=False):
        if require_n8n and not n8n_up["value"]:
            return False
        notified.append({"event": event, "payload": payload, "text": text, "require_n8n": require_n8n})
        return True

    monkeypatch.setattr(notifier, "notify_tier2", fake_notify)

    n8n = FakeN8n()
    monkeypatch.setattr(notifier, "_http", httpx.AsyncClient(transport=httpx.MockTransport(n8n.handler)))

    sb.sops = [{"doc_id": "SOP-NET-001", "title": "VPN", "system_name": "GlobalProtect", "os_platform": "any",
                "content": "1. Disconnect and reconnect", "similarity": 0.81}]
    return {"sb": sb, "claude": claude, "embedded": embedded, "notified": notified, "n8n": n8n, "n8n_up": n8n_up}
