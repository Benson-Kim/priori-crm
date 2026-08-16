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

# Passenger's working directory is not guaranteed to be this file's directory,
# and `app` is a package alongside it. This must run before the imports below,
# which is why they are not at the top of the module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from a2wsgi import ASGIMiddleware

from app.main import app as asgi_app

application = ASGIMiddleware(asgi_app)
