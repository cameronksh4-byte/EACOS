"""Fire-and-forget tasks that never get lost silently."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], label: str) -> asyncio.Task[Any]:
    """Run `coro` in the background, keeping a reference and logging failures."""

    async def runner() -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001
            logger.exception("background task failed: %s", label)

    task = asyncio.create_task(runner(), name=label)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain(timeout: float = 10.0) -> None:
    """Wait for in-flight tasks (used at shutdown and in tests)."""
    if _tasks:
        await asyncio.wait(set(_tasks), timeout=timeout)
