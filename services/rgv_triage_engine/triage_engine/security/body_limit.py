"""ASGI middleware that caps request body size before any handler runs."""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class PayloadTooLargeError(Exception):
    """Raised internally when the streamed body crosses the limit."""


class BodySizeLimitMiddleware:
    """Reject requests whose body exceeds ``max_bytes`` with HTTP 413.

    Two layers of enforcement:

    1. A declared ``Content-Length`` above the limit is rejected immediately,
       before a single body byte is read.
    2. The body is counted as it streams, so chunked uploads or a lying
       ``Content-Length`` cannot exceed the limit either. The app never sees
       more than ``max_bytes`` bytes.

    Args:
        app: Downstream ASGI application.
        max_bytes: Maximum accepted body size in bytes.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await _reject(send, 400, "invalid content-length")
                    return
                if declared > self.max_bytes:
                    await _reject(send, 413, "payload too large")
                    return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise PayloadTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except PayloadTooLargeError:
            if not response_started:
                await _reject(send, 413, "payload too large")


async def _reject(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
