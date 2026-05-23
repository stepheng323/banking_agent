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


def test_telegram_mini_app_html_renders_brand_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "app_name_short", "Aurora")
    client = TestClient(app)

    response = client.get("/static/telegram/onboarding.html")

    assert response.status_code == 200
    assert '<div class="mini-app-avatar" aria-hidden="true">A</div>' in response.text
    assert '<h1 class="mini-app-title">Aurora</h1>' in response.text
    assert "{app_name_short}" not in response.text

    asset_response = client.get("/static/telegram/miniapp_theme.js")
    assert asset_response.status_code == 200
    assert "TelegramMiniAppTheme" in asset_response.text
