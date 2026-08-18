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

# ASGIMiddleware.__init__ calls asyncio.new_event_loop() and immediately starts a
# daemon thread running loop.run_forever(). Building it at import time — the
# obvious `application = ASGIMiddleware(asgi_app)` — hangs every request on this
# host, because the LiteSpeed runner (lswsgi) imports this module and then forks
# its workers. An event loop created before that fork is unusable in the child:
# the selector and self-pipe are shared with the parent, so the child's
# call_soon_threadsafe never wakes the child's own loop.
#
# The symptom is not a crash. The request thread blocks forever on a future that
# will never resolve, LiteSpeed gives up at its connection timeout, and the
# caller sees a 500 after ~120s with nothing in the log but the process being
# reaped. Confirmed with faulthandler against a live worker:
#
#   Thread (loop):    selectors.select -> run_forever          [idle]
#   Current thread:   futures/_base.py:451 result()
#                     a2wsgi/asgi.py:214 execute_in_loop
#                     a2wsgi/asgi.py:225 __call__              [waiting]
#
# Building it on first use pins the loop and its thread to the process that will
# actually serve the request. The pid check re-builds after a fork rather than
# inheriting a broken loop; the lock keeps two threads in one worker from racing.
_build_lock = threading.Lock()
_state: dict = {"pid": None, "app": None}


def application(environ, start_response):
    """WSGI entrypoint; builds the ASGI bridge per process, after any fork."""
    pid = os.getpid()
    if _state["pid"] != pid:
        with _build_lock:
            if _state["pid"] != pid:
                _state["app"] = ASGIMiddleware(asgi_app)
                _state["pid"] = pid
    return _state["app"](environ, start_response)
