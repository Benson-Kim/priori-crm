"""Context-aware access control (ABAC + zero trust), issue #67.

Layers on top of the existing RBAC gates (``require_role`` /
``require_privileged``) and the ADR-0011 platform/tenant authority split —
never replacing or widening them. Submodules (import directly from them):

- ``sensitivity``  — data-sensitivity classification of API resources
- ``context``      — per-request access-context assembly
- ``engine``       — the ABAC policy engine (allow / deny / challenge)
- ``enforcement``  — the app-wide zero-trust gate + decision auditing
- ``db_guard``     — DB-layer fail-closed guard (no implicit trust)
"""
