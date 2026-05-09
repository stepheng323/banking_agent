"""Ingress guardrails for webhook endpoints."""


def require_webhook_ingress_enabled(endpoint: str) -> None:
    """Retained compatibility hook; ingress is enabled whenever gateway is running."""
    del endpoint
