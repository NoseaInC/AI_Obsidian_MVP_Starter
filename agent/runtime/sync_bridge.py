from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any


_DONE = object()


def sync_iter_async(
    factory,
) -> Iterator[dict[str, Any]]:
    """Bridge an async generator to the existing synchronous NDJSON server."""
    output: queue.Queue[Any] = queue.Queue(maxsize=128)
    stop = threading.Event()

    async def produce() -> None:
        try:
            async for item in factory():
                if stop.is_set():
                    break
                output.put(item)
        except BaseException as error:
            output.put(error)
        finally:
            output.put(_DONE)

    def worker() -> None:
        asyncio.run(produce())

    thread = threading.Thread(
        target=worker,
        name="assistant-pydantic-runtime",
        daemon=True,
    )
    thread.start()
    try:
        while True:
            item = output.get()
            if item is _DONE:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        thread.join(timeout=3)
