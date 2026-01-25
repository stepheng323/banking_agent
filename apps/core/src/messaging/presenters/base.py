"""Presenter Protocol and Context definitions."""

from dataclasses import dataclass, field
from typing import Protocol

from apps.core.src.agent.orchestrator.models.intents import UiIntent


@dataclass
class PresentationContext:
    """Context for the presentation layer."""

    channel: str
    phone_number: str
    capabilities: dict[str, bool] = field(default_factory=dict)
    # Allows Presenters to know if they can use Flows, rich media, etc.


class Presenter(Protocol):
    """Protocol for Channel Presenters."""

    async def present(self, intents: list[UiIntent], context: PresentationContext) -> None:
        """
        Render a list of UI intents to the user via the specific channel.

        Args:
            intents: List of abstract UI intents from the Orchestrator.
            context: Channel capability context.
        """
        ...
