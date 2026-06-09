"""W3.7 - security headers (MW-SEC-1) and CORS allow-lists (MW-SEC-2)."""

from app.lib.config import settings


def test_security_headers_present_on_response(client):
    resp = client.get("/api/v1/ping")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_hsts_absent_in_development(client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    resp = client.get("/api/v1/ping")
    assert "Strict-Transport-Security" not in resp.headers


def test_cors_methods_and_headers_are_explicit_not_wildcard():
    methods = settings.cors_allow_methods_list
    headers = settings.cors_allow_headers_list
    assert "*" not in methods
    assert "*" not in headers
    assert "GET" in methods and "POST" in methods and "OPTIONS" in methods
    assert "Authorization" in headers and "Content-Type" in headers
