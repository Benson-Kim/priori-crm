"""Passenger entrypoint for the MochaHost (cPanel) staging deployment.

cPanel's "Setup Python App" runs the app under Phusion Passenger, which requires
a **WSGI** callable named `application`. This app is ASGI-only (FastAPI), so
a2wsgi adapts it. That is the sole reason `a2wsgi` is in requirements.txt; no
other deployment imports this module.

Mount point matters. The cPanel app is registered with Application URL `/api`,
so Passenger hands the app SCRIPT_NAME=/api and PATH_INFO=/v1/health for a
request to /api/v1/health. Starlette strips the mount prefix before matching
routes, so staging must run with API_V1_PREFIX=/v1 — the app's routes then match
and the public URL stays /api/v1/... exactly as in production. Leaving the
production value of /api/v1 here yields a 404 on every route.

See docs/operations/deployment.md §4.1 for the tested matrix.

Fork safety is the other thing that matters, and it is why `application` is a
function instead of a module-level `ASGIMiddleware(...)`. a2wsgi's
ASGIMiddleware starts a background thread running an asyncio event loop *in
its constructor*. Passenger's default "smart" spawning imports this module
once in a preloader process and then fork()s the worker processes from it —
and threads do not survive fork. A module-level middleware therefore leaves
every forked worker holding an event loop that no thread is running: each
request enqueues onto that dead loop and blocks forever in an untimed
threading wait. Externally that is a request that never sends a byte —
`curl: (28) Operation timed out ... 0 bytes received` on every attempt, never
a 404 — while the same module works perfectly when imported and served in a
single process (which is exactly how it was verified in deployment.md §4.1).
Building the middleware lazily, keyed on the current PID, means each worker
constructs its own middleware — and with it a live event-loop thread — after
the fork, in the process that will actually serve requests.
"""

import os
import sys
import threading

# Passenger's working directory is not guaranteed to be this file's directory,
# and `app` is a package alongside it. This must run before the imports below,
# which is why they are not at the top of the module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from a2wsgi import ASGIMiddleware

from app.main import app as asgi_app

# Importing app.main at preload time is safe (it creates no threads and opens
# no connections) and keeps smart spawning's copy-on-write memory sharing.
# Only the ASGIMiddleware construction must be deferred to post-fork.
_lock = threading.Lock()
_wsgi_app = None
_wsgi_app_pid = None


def application(environ, start_response):
    """WSGI entrypoint. Builds the ASGI bridge lazily, once per process."""
    global _wsgi_app, _wsgi_app_pid
    if _wsgi_app is None or _wsgi_app_pid != os.getpid():
        with _lock:
            if _wsgi_app is None or _wsgi_app_pid != os.getpid():
                _wsgi_app = ASGIMiddleware(asgi_app)
                _wsgi_app_pid = os.getpid()
    return _wsgi_app(environ, start_response)
