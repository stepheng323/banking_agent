"""Shared helpers for runtime dependency composition."""

from shared.clients.abstractions.messaging import MessagingClient
from shared.clients.disabled_messaging import DisabledMessagingClient
from shared.clients.telegram.client import TelegramClient
from shared.clients.whatsapp.client import WhatsAppClient


def build_messaging_clients(redis_client: object | None = None) -> dict[str, MessagingClient]:
    """Construct messaging clients used by chat and receipt worker runtimes."""
    clients: dict[str, MessagingClient] = {}

    try:
        clients["whatsapp"] = WhatsAppClient()
    except ValueError:
        clients["whatsapp"] = DisabledMessagingClient("whatsapp")

    try:
        clients["telegram"] = TelegramClient(redis_client=redis_client)
    except ValueError:
        clients["telegram"] = DisabledMessagingClient("telegram")

    return clients
