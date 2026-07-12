from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

from app.observability.logging import app_logger

_SENTINEL = object()
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()

StreamEvent = tuple[str, dict[str, Any]]


async def shielded_stream(
    generator: AsyncIterator[StreamEvent],
) -> AsyncIterator[StreamEvent]:
    """
    Consume *generator* in a background task, yielding events via a queue.
    """
    queue: asyncio.Queue[StreamEvent | object] = asyncio.Queue()
    subscriber_attached = True

    async def _consume() -> None:
        try:
            async for event in generator:
                if subscriber_attached:
                    queue.put_nowait(event)
        except Exception as exc:
            app_logger.exception("Shielded agent task failed: %s", exc)
            if subscriber_attached:
                queue.put_nowait(("error", {"message": str(exc)}))
        finally:
            if subscriber_attached:
                queue.put_nowait(_SENTINEL)

    task = asyncio.create_task(_consume())
    _BACKGROUND_TASKS.add(task)

    def _log_unhandled(t: asyncio.Task[None]) -> None:
        _BACKGROUND_TASKS.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            app_logger.error("Background agent task raised: %s", exc, exc_info=exc)

    task.add_done_callback(_log_unhandled)

    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            yield cast(StreamEvent, item)
    finally:
        # Detach only the transport subscriber. The registry keeps the producer
        # task alive, and future transient events no longer accumulate in RAM.
        subscriber_attached = False
        while not queue.empty():
            queue.get_nowait()
