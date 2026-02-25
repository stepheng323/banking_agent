"""Map outbox entries to UI intents."""

from typing import Any

from apps.core.src.agent.orchestrator.models.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowOptions,
    ShowReceipt,
    UiIntent,
    reconstruct_intent,
)


def map_outbox_to_intents(outbox: list[dict[str, Any]], response_text: str | None) -> list[UiIntent]:
    """Convert raw outbox dicts to UiIntent objects."""
    intents: list[UiIntent] = []

    for item in outbox:
        intent = reconstruct_intent(item)
        if intent:
            intents.append(intent)

    has_primary_interaction = any(
        isinstance(i, (RequestAuth, RequestConfirmation, ShowReceipt, ShowOptions)) for i in intents
    )
    if response_text and not has_primary_interaction and not any(isinstance(i, Say) for i in intents):
        intents.append(Say(text=response_text))

    return intents
