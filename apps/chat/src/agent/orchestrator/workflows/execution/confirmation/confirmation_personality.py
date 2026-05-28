"""Personality context selection for confirmation prompts."""

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from shared.i18n.personality import PersonalityContext, transfer_personality_context_from_payload


def _confirmation_personality_context(
    state: OrchestratorState,
    confirm_task_ids: list[str],
) -> PersonalityContext | None:
    if len(confirm_task_ids) != 1:
        return None

    confirmation_task = state.tasks.get(confirm_task_ids[0])
    if confirmation_task and confirmation_task.type == "transfer":
        return transfer_personality_context_from_payload(
            confirmation_task.payload,
            moment="confirmation",
        )
    if confirmation_task and confirmation_task.type in {"airtime", "data"}:
        return PersonalityContext(
            moment="confirmation",
            amount=confirmation_task.payload.get("amount"),
            saved_recipient=bool(
                confirmation_task.payload.get("beneficiary_id") or confirmation_task.payload.get("is_self")
            ),
        )
    return None


__all__ = ["_confirmation_personality_context"]
