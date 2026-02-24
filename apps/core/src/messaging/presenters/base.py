"""Presenter Protocol and Context definitions."""

from typing import Any, Protocol

from pydantic import BaseModel, Field

from apps.core.src.agent.orchestrator.models.intents import UiIntent


class PresentationContext(BaseModel):
    """Context passed to presenters."""

    channel: str
    phone_number: str
    capabilities: dict[str, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Allows Presenters to know if they can use Flows, rich media, etc.


class PresentationResult(BaseModel):
    """Result of attempting to present intents to a channel."""

    success: bool = True
    message_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class Presenter(Protocol):
    """Protocol for Channel Presenters."""

    async def present(self, intents: list[UiIntent], context: PresentationContext) -> PresentationResult:
        """
        Render a list of UI intents to the user via the specific channel.

        Args:
            intents: List of abstract UI intents from the Orchestrator.
            context: Channel capability context.

        Returns:
            PresentationResult containing success status and generated message IDs.
        """
        ...
