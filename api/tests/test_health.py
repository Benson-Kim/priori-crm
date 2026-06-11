"""Tests for the /health contract (#28).

The basic /health endpoint is a load-balancer probe. Its canonical shape is
exactly status / version / environment / timestamp — no service-name field
and no infrastructure detail (those live on the secret-gated
/health/detailed). These assertions pin that contract by design so the
endpoint and test can no longer drift apart silently.
"""

from app.lib.config import settings


def test_health_check(client):
    """/health returns the canonical, minimal contract."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()

    # Required fields and their intended meaning.
    assert data["status"] == "healthy"
    assert data["version"] == settings.APP_VERSION
    assert data["environment"] == settings.ENVIRONMENT
    assert "timestamp" in data

    # The contract is exactly these four keys — nothing more leaks out.
    assert set(data.keys()) == {"status", "version", "environment", "timestamp"}

    # Guard against regressing to the old, never-served `app` key the test
    # was originally (wrongly) asserting.
    assert "app" not in data
