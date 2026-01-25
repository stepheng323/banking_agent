"""Presenter Factory for creating channel-specific presenters."""

from typing import Type

from apps.core.src.messaging.presenters.base import Presenter
from apps.core.src.messaging.presenters.whatsapp import WhatsAppPresenter
from shared.clients.abstractions.messaging import MessagingClient
from shared.config import settings


class PresenterFactory:
    """Factory to create presenters based on channel."""

    _registry: dict[str, Type[Presenter]] = {
        "whatsapp": WhatsAppPresenter,
    }

    @classmethod
    def register(cls, channel: str, presenter_cls: Type[Presenter]) -> None:
        """Register a new presenter for a channel."""
        cls._registry[channel] = presenter_cls

    @classmethod
    def create(cls, channel: str, client: MessagingClient) -> Presenter:
        """Create a presenter instance for the given channel."""
        presenter_cls = cls._registry.get(channel)
        
        if not presenter_cls:
            # Fallback to default channel (whatsapp) if available, or error
            default = settings.default_channel
            presenter_cls = cls._registry.get(default)
            
        if not presenter_cls:
            # If still nothing, default to WhatsAppPresenter hardcoded as safety net
            # or raise ValueError("No presenter for channel")
            presenter_cls = WhatsAppPresenter

        return presenter_cls(client)
