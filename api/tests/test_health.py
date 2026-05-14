def test_health_check(client):
    """Test the health endpoint returns expected structure."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert data["app"] == "Priori Technologies"
    assert data["version"] == "1.0.188-288"
    assert "status" in data
