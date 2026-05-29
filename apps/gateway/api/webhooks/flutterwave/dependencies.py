"""Flutterwave webhook dependency construction."""

from apps.gateway.api.webhooks.flutterwave.service import FlutterwaveWebhookService
from shared.queue.factory import QueuePublisherFactory

_service_instance: FlutterwaveWebhookService | None = None


def get_flutterwave_webhook_service() -> FlutterwaveWebhookService:
    """Get or create Flutterwave webhook service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = FlutterwaveWebhookService(publisher=QueuePublisherFactory.get_async_publisher())
    return _service_instance
