"""Ingress ownership guardrails for webhook endpoints."""

from fastapi import HTTPException, status

from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def require_webhook_ingress_enabled(endpoint: str) -> None:
    """Reject webhook traffic when this runtime is not the active ingress owner."""
    if settings.enable_webhook_ingress:
        return
    logger.warning(
        "webhook_ingress_disabled",
        endpoint=endpoint,
        stack_role=settings.runtime_stack_role,
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Webhook ingress disabled on this runtime",
    )
