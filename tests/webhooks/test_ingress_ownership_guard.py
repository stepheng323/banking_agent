import pytest
from fastapi import HTTPException

from apps.gateway.api.webhooks.ownership import require_webhook_ingress_enabled
from shared.config.settings import settings


def test_webhook_ingress_guard_raises_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "enable_webhook_ingress", False)

    with pytest.raises(HTTPException) as exc:
        require_webhook_ingress_enabled("telegram")

    assert exc.value.status_code == 503


def test_webhook_ingress_guard_allows_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "enable_webhook_ingress", True)
    require_webhook_ingress_enabled("telegram")
