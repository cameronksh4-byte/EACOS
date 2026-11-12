"""Tier gates for routes (FastAPI dependencies) and services (decorator).

Routes:
    @app.get("/v1/thing", dependencies=[Depends(require_feature(Feature.X))])
A route whose feature is above the deployment's tier, or kill-switched, returns
404. Locked features look as if they don't exist, rather than advertising an
upsell surface to callers. List the feature gate *before* any auth dependency,
so a locked route answers 404 rather than 401 (FastAPI runs them in order).

Services:
    @gated(Feature.CONTEXT_ENRICHMENT, default=None)
    async def enrich(...): ...
A gated coroutine returns `default` (or `default()` if it's callable) without
running when its feature is off, so callers never branch on tiers themselves.
"""

from __future__ import annotations

import functools
import hmac
from typing import Any, Awaitable, Callable, TypeVar

from fastapi import Header, HTTPException, Request

from app.config import Feature, get_settings
from app.services import signing

R = TypeVar("R")


def gated(feature: Feature, *, default: Any = None):
    def decorator(fn: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> R:
            if not get_settings().feature_enabled(feature):
                return default() if callable(default) else default
            return await fn(*args, **kwargs)

        wrapper.__gated_feature__ = feature  # type: ignore[attr-defined]
        return wrapper

    return decorator


def require_feature(feature: Feature) -> Callable[[], None]:
    def dependency() -> None:
        if not get_settings().feature_enabled(feature):
            raise HTTPException(status_code=404, detail="Not Found")

    dependency.__name__ = f"require_{feature.value}"
    return dependency


def require_webhook_secret(x_webhook_secret: str = Header(...)) -> None:
    expected = get_settings().webhook_secret.get_secret_value()
    if not expected or not hmac.compare_digest(x_webhook_secret.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def require_tenant(tenant_id: str) -> None:
    if not get_settings().is_tenant_allowed(tenant_id):
        raise HTTPException(status_code=403, detail="tenant not served by this deployment")


async def require_n8n_signature(request: Request) -> bytes:
    """Verify an inbound n8n callback (HMAC over timestamp + raw body). Returns the body."""
    settings = get_settings()
    body = await request.body()
    if not signing.verify(
        settings.n8n_callback_secret.get_secret_value(),
        body,
        request.headers.get(signing.SIGNATURE_HEADER, ""),
        settings.webhook_signature_max_skew_seconds,
    ):
        raise HTTPException(status_code=401, detail="invalid signature")
    return body
