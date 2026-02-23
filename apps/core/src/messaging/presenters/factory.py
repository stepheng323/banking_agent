"""Presenter Factory for creating channel-specific presenters."""

from apps.core.src.messaging.presenters.base import Presenter
from apps.core.src.messaging.presenters.telegram import TelegramPresenter
from apps.core.src.messaging.presenters.whatsapp import WhatsAppPresenter
from shared.clients.abstractions.messaging import MessagingClient
from shared.config import settings


class PresenterFactory:
    """Factory to create presenters based on channel."""

    _registry: dict[str, type[Presenter]] = {
        "whatsapp": WhatsAppPresenter,
        "telegram": TelegramPresenter,
    }

    @classmethod
    def register(cls, channel: str, presenter_cls: type[Presenter]) -> None:
        """Register a new presenter for a channel."""
        cls._registry[channel] = presenter_cls

    @classmethod
    def create(cls, channel: str, client: MessagingClient) -> Presenter:
        """Create a presenter instance for the given channel."""
        presenter_cls = cls._registry.get(channel)

        if not presenter_cls:
            default = settings.default_channel
            presenter_cls = cls._registry.get(default)

        if not presenter_cls:
            presenter_cls = WhatsAppPresenter

        return presenter_cls(client)
