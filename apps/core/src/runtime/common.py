"""Shared helpers for runtime dependency composition."""

from shared.clients.abstractions.messaging import MessagingClient
from shared.clients.telegram.client import TelegramClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings


def require_aws_account_id() -> None:
    """Ensure the AWS account id is present for AWS-backed runtimes."""
    if not settings.aws_account_id:
        raise RuntimeError("AWS_ACCOUNT_ID is required for AWS-only queue runtime")


def build_messaging_clients() -> dict[str, MessagingClient]:
    """Construct messaging clients used by chat and messaging worker runtimes."""
    return {
        "whatsapp": WhatsAppClient(),
        "telegram": TelegramClient(),
    }

