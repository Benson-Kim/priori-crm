"""Concurrency gate + off-thread runner for heavy export/PDF generation.

Excel/PDF generation is CPU- and memory-bound and was run synchronously in
the request handlers with no cap. This module bounds how many such
generations run at once per process and moves the blocking work off the
event loop (complements the ISSUE-014 export hardening):

- ``run_export(fn, *args)`` acquires a slot from a process-wide
  ``anyio.CapacityLimiter`` (sized from ``settings.EXPORT_MAX_CONCURRENCY``)
  *without* queueing. If every slot is taken it raises
  ``ServiceUnavailableException`` (HTTP 503 + Retry-After) so callers shed
  load instead of piling up more in-memory workbooks.
- The generation itself runs in a worker thread via
  ``anyio.to_thread.run_sync`` so the CPU work never blocks the event loop.

A request-scoped sync SQLAlchemy ``Session`` may be used inside ``fn``: the
handler awaits the result, so the session is only ever touched by one thread
at a time (sequential cross-thread use — the same execution model as a sync
``def`` endpoint running in Starlette's threadpool).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache

import anyio

from app.common.exceptions import ServiceUnavailableException
from app.lib.config import settings

logger = logging.getLogger(__name__)

# Suggested backoff (seconds) handed to the client when the box is saturated.
_RETRY_AFTER_SECONDS = 5


@lru_cache
def _limiter() -> anyio.CapacityLimiter:
    """Process-wide limiter sized from EXPORT_MAX_CONCURRENCY (built lazily)."""
    return anyio.CapacityLimiter(settings.EXPORT_MAX_CONCURRENCY)


def _reject_if_saturated(limiter: anyio.CapacityLimiter) -> None:
    """Raise 503 if no export slot is free, instead of queueing."""
    if limiter.available_tokens < 1:
        logger.warning(
            "Export rejected: at capacity (%d concurrent)",
            int(limiter.total_tokens),
        )
        raise ServiceUnavailableException(
            detail=(
                "The server is busy generating exports. Please retry in a few seconds."
            ),
            retry_after=_RETRY_AFTER_SECONDS,
        )


async def run_export[T](fn: Callable[..., T], *args: object) -> T:
    """Run a blocking export generator under the concurrency cap, off-thread.

    Acquires a limiter slot without waiting: if the process is already at
    ``EXPORT_MAX_CONCURRENCY`` in-flight generations, raises
    ``ServiceUnavailableException`` rather than blocking a request worker.
    Otherwise runs ``fn(*args)`` in a worker thread and releases the slot.
    """
    limiter = _limiter()
    _reject_if_saturated(limiter)
    async with limiter:
        return await anyio.to_thread.run_sync(fn, *args)
