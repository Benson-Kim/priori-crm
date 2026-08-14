"""Route class that commits the request-scoped session before the response.

Fixes the systemic read-after-write race (QA finding 04): services flush
but never commit, and the commit used to live only in ``get_db()``'s
post-``yield`` teardown. FastAPI runs that teardown after the response has
already started travelling out through the BaseHTTPMiddleware stack, so a
client could receive a 201, immediately GET the new id and hit a 404
because the INSERT was not yet durable. Reproduced live at ~7%.

:class:`CommitOnSuccessRoute` closes that window for every endpoint whose
router uses it: after the endpoint runs and the response object is built —
but strictly BEFORE the response is handed to the middleware stack / sent
to the client — it commits the request's session (published by ``get_db``
on ``request.state.db``).

Semantics preserved:

- Rollback on exception: any exception raised by the endpoint (or by this
  commit) propagates as before, so ``get_db()``'s ``except`` branch still
  rolls the transaction back and exception handlers still produce the
  error response. This route class never commits on the exception path.
- ``get_db_context()`` (non-FastAPI contexts) is untouched.
- Read-only report / sales-desk snapshots: committing a ``REPEATABLE READ
  READ ONLY`` transaction simply ends it — exactly what ``get_db()``'s
  teardown (and the export-audit dependencies' own ``db.commit()``) already
  did after the handler returned. The audit dependencies' post-``yield``
  writes still run afterwards on the same session and commit themselves.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool


class CommitOnSuccessRoute(APIRoute):
    """APIRoute that makes the request's writes durable pre-response.

    Every module router must be constructed with
    ``APIRouter(route_class=CommitOnSuccessRoute)``;
    ``tests/test_read_after_write_commit.py`` enforces this structurally.
    """

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_route_handler = super().get_route_handler()

        async def commit_before_response(request: Request) -> Response:
            response = await original_route_handler(request)

            db = getattr(request.state, "db", None)
            if db is not None:
                # Synchronous SQLAlchemy commit off the event loop — the
                # same treatment FastAPI gives sync dependency teardown.
                await run_in_threadpool(db.commit)

            return response

        return commit_before_response
