"""Token spend tracking and the monthly budget cap.

Each Claude call is written to the `llm_usage` table. Before triage, the
month-to-date spend is read back (cached for a minute) and compared with
MONTHLY_TOKEN_BUDGET_USD. Over budget, tickets skip the model and go straight
to a human, so a traffic spike can't run up the bill.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import get_settings
from app.services import supabase_client

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60.0
_spend_cache: dict[str, tuple[float, float]] = {}  # tenant -> (fetched_at, usd)


def estimate_cost_usd(model: str, usage: Any) -> float:
    """Cost of one response from its `usage` block (thinking is billed as output)."""
    prices = get_settings().model_prices_per_mtok.get(model)
    if prices is None:
        # Unknown model (e.g. a fallback): price it like the costliest one we know.
        prices = max(get_settings().model_prices_per_mtok.values())
    in_price, out_price = prices
    uncached = getattr(usage, "input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    output = getattr(usage, "output_tokens", 0) or 0
    input_cost = uncached * in_price + cache_read * in_price * 0.1 + cache_write * in_price * 1.25
    return (input_cost + output * out_price) / 1_000_000


async def record_usage(tenant_id: str, ticket_id: str, model: str, usage: Any) -> float:
    cost = estimate_cost_usd(model, usage)
    try:
        await supabase_client.insert(
            "llm_usage",
            {
                "tenant_id": tenant_id,
                "ticket_id": ticket_id,
                "model": model,
                "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                "cost_usd": round(cost, 6),
            },
        )
    except Exception:  # noqa: BLE001 - usage logging must never break triage
        logger.exception("failed to record llm usage ticket=%s", ticket_id)
    cached = _spend_cache.get(tenant_id)
    if cached:
        _spend_cache[tenant_id] = (cached[0], cached[1] + cost)
    return cost


async def month_to_date_spend(tenant_id: str) -> float | None:
    """Month-to-date USD spend, or None if it can't be read right now."""
    now = time.monotonic()
    cached = _spend_cache.get(tenant_id)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    try:
        spend = float(await supabase_client.rpc("tenant_month_spend", {"p_tenant_id": tenant_id}) or 0.0)
    except Exception:  # noqa: BLE001
        logger.exception("could not read month-to-date spend tenant=%s", tenant_id)
        return None
    _spend_cache[tenant_id] = (now, spend)
    return spend


async def budget_exhausted(tenant_id: str) -> bool:
    spend = await month_to_date_spend(tenant_id)
    if spend is None:
        # Can't verify: keep serving. RAG will fail too if Supabase is down,
        # which already routes tickets to a human.
        return False
    return spend >= get_settings().monthly_token_budget_usd
