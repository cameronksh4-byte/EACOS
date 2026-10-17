"""FastAPI application factory.

Run with::

    uvicorn --factory triage_engine.main:create_app --host 0.0.0.0 --port 8080 --proxy-headers
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from triage_engine.api import health, packets, webhooks
from triage_engine.config import Settings, get_settings
from triage_engine.container import AppServices, build_services
from triage_engine.logging_config import configure_logging
from triage_engine.security.body_limit import BodySizeLimitMiddleware
from triage_engine.security.headers import SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, services: AppServices | None = None) -> FastAPI:
    """Build the application.

    Args:
        settings: Settings override (defaults to the environment).
        services: Pre-built services (tests inject fakes); when omitted the
            production dependencies are built during startup.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        built = services or await build_services(settings)
        app.state.services = built
        try:
            yield
        finally:
            if services is None:
                await built.aclose()

    app = FastAPI(
        title="RGV Cross-Border Logistics Triage Engine",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # the schema itself is not published either
        lifespan=lifespan,
    )
    app.state.settings = settings
    if services is not None:
        app.state.services = services

    # Middleware runs outermost-last-added: size limit first, then hosts, then headers.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)

    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(packets.router)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        # FastAPI's default echoes the offending input, which may contain PII.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": "invalid request"},
        )

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error: %s", type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "internal error"},
        )

    return app
