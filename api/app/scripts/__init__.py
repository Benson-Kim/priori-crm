"""Operational management scripts shipped inside the API package.

Run with ``python -m app.scripts.<name>`` from the ``api/`` directory (or
any environment where the ``app`` package is importable). These scripts
require direct database access and are deliberately NOT exposed through any
HTTP surface (QA finding 09: no API-reachable privilege escalation).
"""
