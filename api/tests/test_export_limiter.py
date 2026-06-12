"""Export concurrency cap + off-thread execution.

The test suite is synchronous (strict markers, asyncio STRICT mode, no
anyio_backend fixture), so the async limiter helpers are driven through
``anyio.run`` rather than async test functions.
"""

import threading

import anyio
import pytest

import app.common.export_limiter as limiter_mod
from app.common.exceptions import ServiceUnavailableException
from app.common.export_limiter import run_export


@pytest.fixture(autouse=True)
def _reset_limiter():
    """Each test gets a fresh limiter so capacity state never leaks."""
    limiter_mod._limiter.cache_clear()
    yield
    limiter_mod._limiter.cache_clear()


def test_run_export_runs_generator_and_returns_result():
    async def _scenario():
        return await run_export(lambda a, b: a + b, 2, 3)

    assert anyio.run(_scenario) == 5


def test_run_export_runs_off_the_event_loop():
    # The generator must execute in a worker thread, never on the loop
    # thread, so CPU-bound builds cannot stall other requests.
    loop_thread = threading.current_thread()

    async def _scenario():
        return await run_export(threading.current_thread)

    worker_thread = anyio.run(_scenario)
    assert worker_thread is not loop_thread


def test_run_export_rejects_with_503_when_saturated(monkeypatch):
    # One slot total; hold it, then a second generation must be refused with
    # a 503 + Retry-After rather than queueing.
    monkeypatch.setattr(
        limiter_mod.settings, "EXPORT_MAX_CONCURRENCY", 1, raising=False
    )
    limiter_mod._limiter.cache_clear()

    async def _scenario():
        limiter = limiter_mod._limiter()
        async with limiter:
            await run_export(lambda: b"x")

    with pytest.raises(ServiceUnavailableException) as exc:
        anyio.run(_scenario)
    assert exc.value.status_code == 503
    assert exc.value.extra.get("retry_after")


def test_slot_is_released_after_use(monkeypatch):
    # After a completed run the slot frees up and the next call succeeds.
    monkeypatch.setattr(
        limiter_mod.settings, "EXPORT_MAX_CONCURRENCY", 1, raising=False
    )
    limiter_mod._limiter.cache_clear()

    async def _scenario():
        first = await run_export(lambda: 1)
        second = await run_export(lambda: 2)
        return first, second

    assert anyio.run(_scenario) == (1, 2)
