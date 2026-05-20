from fastapi.testclient import TestClient

from apps.gateway.main import app, settings


def test_gateway_responses_include_baseline_security_headers(monkeypatch):
    monkeypatch.setattr(settings.runtime, "app_env", "development")

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "https://telegram.org" in response.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in response.headers


def test_gateway_hsts_is_enabled_outside_local(monkeypatch):
    monkeypatch.setattr(settings.runtime, "app_env", "production")

    response = TestClient(app).get("/health")

    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
