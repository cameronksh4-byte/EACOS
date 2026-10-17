"""Liveness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/v1", tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    """Report liveness without revealing versions, dependencies or hostnames."""
    return {"status": "secure_online", "region": request.app.state.settings.aws_region}
