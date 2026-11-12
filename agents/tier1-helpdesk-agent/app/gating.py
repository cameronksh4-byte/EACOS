"""Tier gates and auth as FastAPI dependencies.

    @app.get("/v1/thing", dependencies=[Depends(require_feature(Feature.X))])

A route whose feature is above the deployment's tier, or kill-switched, returns
404. Locked features look as if they don't exist, rather than advertising an
upsell surface to callers.
"""

from __future__ import annotations

import hmac
from typing import Callable

from fastapi import Header, HTTPException

from app.config import Feature, get_settings


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
