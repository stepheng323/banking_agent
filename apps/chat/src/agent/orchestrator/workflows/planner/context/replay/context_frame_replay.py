"""Transaction replay helpers for context-frame follow-up responses."""

from dataclasses import dataclass

from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    ContextFrameStateView,
    context_frame_state_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_accounts import (
    _replay_source_account_override_for_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_targets import (
    replay_target_entities_for_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_tasks import (
    build_context_frame_replay_tasks_for_view,
)
from shared.types.planner import ContextFrameFollowupDecision, ContextFrameReplayModifier


@dataclass(frozen=True, slots=True)
class ContextFrameReplayResult:
    response: str | None = None
    path_shape: str = "context_frame_replay"
    recent_domain_focus: str = "transaction"
    tasks: dict[str, TaskSpec] | None = None
    waves: list[list[str]] | None = None


def build_context_frame_replay_response(
    state: OrchestratorState,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    text: str,
    replay_modifier: ContextFrameReplayModifier | None = None,
) -> ContextFrameReplayResult | None:
    return build_context_frame_replay_response_for_view(
        context_frame_state_view(state),
        frame,
        decision,
        text,
        replay_modifier=replay_modifier,
    )


def build_context_frame_replay_response_for_view(
    state_view: ContextFrameStateView,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    text: str,
    replay_modifier: ContextFrameReplayModifier | None = None,
) -> ContextFrameReplayResult | None:
    if frame.frame_type not in {
        ContextFrameType.TRANSACTION_LIST,
        ContextFrameType.TRANSACTION_DETAIL,
        ContextFrameType.RECEIPT,
    }:
        return None

    source_requested, source_patch, requested_source = _replay_source_account_override_for_view(
        text,
        state_view,
        replay_modifier,
    )
    if source_requested and source_patch is None:
        source_label = requested_source or "that source account"
        return ContextFrameReplayResult(
            response=(
                f"I could not find {source_label!r} among your linked source accounts. "
                "Choose one of your linked accounts and try again."
            ),
            path_shape="context_frame_replay_source_unmatched",
        )

    entities = replay_target_entities_for_view(
        frame,
        decision,
        text=text,
        state_view=state_view,
        replay_modifier=replay_modifier,
    )
    tasks, wave_ids = build_context_frame_replay_tasks_for_view(
        state_view=state_view,
        entities=entities,
        text=text,
        source_patch=source_patch,
        replay_modifier=replay_modifier,
    )

    if not wave_ids:
        return None

    return ContextFrameReplayResult(tasks=tasks, waves=[wave_ids])


__all__ = [
    "ContextFrameReplayResult",
    "build_context_frame_replay_response",
    "build_context_frame_replay_response_for_view",
]
