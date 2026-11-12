"""Single gateway for every Claude call: provider, budget cap, structured output.

Hard guardrail #3 lives here. Before *any* model invocation (vision, triage,
SOP synthesis), the call's worst-case cost is reserved atomically against the
tenant's monthly cap with `try_reserve_llm_budget`. After the call, the
reservation is settled to the real cost. If the cap would be exceeded, or the
ledger can't be reached to check, the call is not made (fail closed).

Each response is constrained with `output_config.format`, checked for
`stop_reason` (refusal, max_tokens) before its content is read, and then
validated again with Pydantic.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import LLMProvider, get_settings
from app.services import supabase_client

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
T = TypeVar("T", bound=BaseModel)


class BudgetExceededError(RuntimeError):
    """The tenant's monthly cap would be exceeded by this call."""


class BudgetUnavailableError(RuntimeError):
    """The spend ledger couldn't be reached, so the cap can't be verified."""


@dataclass
class CallRecord:
    purpose: str
    model: str
    cost_usd: float
    outcome: str


@dataclass
class CostLedger:
    """Per-ticket accounting, reported in telemetry."""

    calls: list[CallRecord] = field(default_factory=list)

    @property
    def total_usd(self) -> float:
        return round(sum(c.cost_usd for c in self.calls), 6)

    @property
    def last_model(self) -> str | None:
        return self.calls[-1].model if self.calls else None


@dataclass
class StructuredResult(Generic[T]):
    model: str
    output: T | None = None
    refused: bool = False
    error: str | None = None
    cost_usd: float = 0.0


_client: Any = None


def get_client() -> Any:
    global _client
    if _client is None:
        settings = get_settings()
        if settings.llm_provider is LLMProvider.BEDROCK:
            _client = anthropic.AsyncAnthropicBedrockMantle(
                aws_region=settings.aws_region, timeout=settings.llm_timeout_seconds, max_retries=2
            )
        else:
            _client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
                timeout=settings.llm_timeout_seconds,
                max_retries=2,
            )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def cost_from_usage(model: str, usage: Any) -> float:
    """Real cost of one response (thinking tokens bill as output)."""
    in_price, out_price = get_settings().price_for(model)
    uncached = getattr(usage, "input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    output = getattr(usage, "output_tokens", 0) or 0
    input_cost = uncached * in_price + cache_read * in_price * 0.1 + cache_write * in_price * 1.25
    return (input_cost + output * out_price) / 1_000_000


def worst_case_cost(model: str, max_tokens: int, system: str, content: list[dict[str, Any]]) -> float:
    """Upper bound used for the reservation: rough input tokens + full max_tokens."""
    settings = get_settings()
    in_price, out_price = settings.price_for(model)
    chars = len(system)
    images = 0
    for block in content:
        if block.get("type") == "text":
            chars += len(block.get("text", ""))
        elif block.get("type") == "image":
            images += 1
    # ~3 chars/token is conservative for English. Cache writes bill at 1.25x.
    input_tokens = chars / 3 + images * settings.reserve_tokens_per_image + 200
    return round((input_tokens * in_price * 1.25 + max_tokens * out_price) / 1_000_000, 6)


# ---------------------------------------------------------------------------
# Budget reservation (guardrail #3)
# ---------------------------------------------------------------------------


async def reserve(tenant_id: str, ticket_id: str, purpose: str, estimated_usd: float) -> str:
    settings = get_settings()
    try:
        reservation_id = await supabase_client.rpc(
            "try_reserve_llm_budget",
            {
                "p_tenant_id": tenant_id,
                "p_ticket_id": ticket_id,
                "p_purpose": purpose,
                "p_estimated_usd": estimated_usd,
                "p_cap_usd": settings.budget_for(tenant_id),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("budget ledger unreachable tenant=%s err=%s", tenant_id, type(exc).__name__)
        raise BudgetUnavailableError("spend ledger unreachable") from exc
    if not reservation_id:
        logger.warning("monthly budget cap reached tenant=%s purpose=%s", tenant_id, purpose)
        raise BudgetExceededError(f"monthly budget reached for {tenant_id}")
    return str(reservation_id)


async def settle(tenant_id: str, reservation_id: str, model: str, usage: Any, cost: float) -> None:
    try:
        await supabase_client.rpc(
            "settle_llm_budget",
            {
                "p_tenant_id": tenant_id,
                "p_reservation_id": reservation_id,
                "p_model": model,
                "p_cost_usd": round(cost, 6),
                "p_input_tokens": getattr(usage, "input_tokens", 0) or 0,
                "p_output_tokens": getattr(usage, "output_tokens", 0) or 0,
                "p_cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                "p_cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to settle llm reservation id=%s tenant=%s", reservation_id, tenant_id)


# ---------------------------------------------------------------------------
# Structured call
# ---------------------------------------------------------------------------


_SCHEMAS: dict[type, dict[str, Any]] = {}


def output_format(model_cls: type[BaseModel]) -> dict[str, Any]:
    if model_cls not in _SCHEMAS:
        _SCHEMAS[model_cls] = {"type": "json_schema", "schema": anthropic.transform_schema(model_cls)}
    return _SCHEMAS[model_cls]


async def structured_call(
    *,
    tenant_id: str,
    ticket_id: str,
    purpose: str,
    model: str,
    effort: str,
    max_tokens: int,
    system: str,
    content: list[dict[str, Any]],
    output_model: type[T],
    ledger: CostLedger,
    allow_fallbacks: bool = False,
    preprocess: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> StructuredResult[T]:
    """Budget-gated, schema-constrained call. Raises Budget*Error before calling."""
    settings = get_settings()
    reservation_id = await reserve(tenant_id, ticket_id, purpose, worst_case_cost(model, max_tokens, system, content))

    request: dict[str, Any] = {
        "model": settings.provider_model_id(model),
        "max_tokens": max_tokens,
        # Frozen system text first, so the prompt cache can reuse it.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": effort, "format": output_format(output_model)},
    }
    result: StructuredResult[T] = StructuredResult(model=model)
    usage: Any = None
    try:
        client = get_client()
        # Server-side refusal fallbacks exist on the Claude API only.
        if allow_fallbacks and settings.enable_refusal_fallbacks and settings.llm_provider is LLMProvider.ANTHROPIC:
            response = await client.beta.messages.create(**request, betas=[FALLBACK_BETA], fallbacks="default")
        else:
            response = await client.messages.create(**request)
    except anthropic.APIStatusError as exc:
        result.error = f"api_status_{exc.status_code}"
        logger.warning("claude %s failed model=%s status=%s request_id=%s", purpose, model, exc.status_code, exc.request_id)
    except anthropic.APIConnectionError as exc:  # includes APITimeoutError
        result.error = type(exc).__name__
        logger.warning("claude %s connection error model=%s err=%s", purpose, model, result.error)
    else:
        usage = response.usage
        result.model = (response.model or model).removeprefix(settings.bedrock_model_prefix)
        _read_response(response, output_model, result, purpose, max_tokens, preprocess)
    finally:
        result.cost_usd = cost_from_usage(result.model, usage) if usage is not None else 0.0
        await settle(tenant_id, reservation_id, result.model, usage, result.cost_usd)
        outcome = "refused" if result.refused else (result.error or "ok")
        ledger.calls.append(CallRecord(purpose, result.model, result.cost_usd, outcome))
    return result


def _read_response(
    response: Any,
    output_model: type[T],
    result: StructuredResult[T],
    purpose: str,
    max_tokens: int,
    preprocess: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> None:
    # Check stop_reason before reading content.
    if response.stop_reason == "refusal":
        result.refused = True
        details = getattr(response, "stop_details", None)
        logger.info("claude %s refusal model=%s category=%s", purpose, result.model, getattr(details, "category", None))
        return
    if response.stop_reason == "max_tokens":
        result.error = "max_tokens"
        logger.warning("claude %s output truncated model=%s max_tokens=%d", purpose, result.model, max_tokens)
        return
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        result.error = "no_text_block"
        return
    try:
        data = json.loads(text)
        if preprocess is not None:
            data = preprocess(data)
        result.output = output_model.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        result.error = "invalid_output"
        logger.warning("claude %s output failed validation model=%s err=%s", purpose, result.model, type(exc).__name__)
