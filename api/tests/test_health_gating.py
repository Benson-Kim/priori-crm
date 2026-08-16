"""/health stays public, /health/detailed requires the internal secret."""

import app.lib.config as config_module


def test_basic_health_is_public(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_detailed_health_refused_without_secret(client, monkeypatch):
    # No internal secret configured -> endpoint fails closed.
    monkeypatch.setattr(
        config_module.settings, "INTERNAL_API_SECRET", "", raising=False
    )
    resp = client.get("/api/v1/health/detailed")
    assert resp.status_code in (401, 403)


def test_detailed_health_allowed_with_secret(client, monkeypatch):
    monkeypatch.setattr(
        config_module.settings, "INTERNAL_API_SECRET", "s3cret-value", raising=False
    )
    resp = client.get(
        "/api/v1/health/detailed",
        headers={"X-Internal-Secret": "s3cret-value"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "pool" in body and "database" in body


def test_detailed_health_is_not_public_to_abac(client, monkeypatch):
    """#67 review F6: /health/detailed must not ride /health's PUBLIC exemption.

    The prefix rule classified everything under /health as PUBLIC, so the
    internal-secret-gated pool/DB internals bypassed ABAC entirely — a
    caller holding the secret could read them even from a denylisted IP.
    """
    monkeypatch.setattr(
        config_module.settings, "INTERNAL_API_SECRET", "s3cr3t-value", raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "ABAC_IP_DENYLIST", "testclient", raising=False
    )
    resp = client.get(
        "/api/v1/health/detailed",
        headers={"X-Internal-Secret": "s3cr3t-value"},
    )
    assert resp.status_code == 403
    assert "policy" in resp.json()["error"].lower()

    # The exact /health probe keeps its PUBLIC exemption: a load balancer
    # must never be denied.
    assert client.get("/api/v1/health").status_code == 200
