from apps.gateway.api.webhooks.ownership import require_webhook_ingress_enabled


def test_webhook_ingress_guard_is_noop() -> None:
    require_webhook_ingress_enabled("telegram")
