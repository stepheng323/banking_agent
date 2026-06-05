"""Target selection for context-frame transaction replay."""

from decimal import Decimal

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_filtering import (
    find_filtered_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_search import (
    find_matching_entities,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    ContextFrameStateView,
    context_frame_state_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_text import (
    amount_reference_values,
    normalize,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_accounts import (
    _loaded_accounts_for_view,
    _replay_source_account_candidate,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_amounts import (
    _replay_amount_override,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_modifier_core import (
    _modifier_amount_override,
    _modifier_source_account_candidate,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_narration import (
    _replay_narration_override,
)
from shared.types.planner import ContextFrameFollowupDecision, ContextFrameReplayModifier


def _decision_target_text(decision: ContextFrameFollowupDecision) -> str:
    return (decision.target_text or "").strip()


def _target_text_is_replay_amount_override(
    decision: ContextFrameFollowupDecision,
    text: str,
    replay_modifier: ContextFrameReplayModifier | None,
) -> bool:
    target_text = _decision_target_text(decision)
    if not target_text:
        return False

    override = _replay_amount_override(text)
    if override is None:
        override = _modifier_amount_override(text, replay_modifier)
    if override is None:
        return False

    target_amounts = amount_reference_values(target_text)
    return len(target_amounts) == 1 and any(abs(amount - override) < Decimal("0.01") for amount in target_amounts)


def _modifier_matches_target_text(modifier: str | None, target_text: str) -> bool:
    if not modifier:
        return False

    normalized_modifier = normalize(modifier)
    normalized_target = normalize(target_text)
    if not normalized_modifier or not normalized_target:
        return False
    if normalized_modifier == normalized_target:
        return True
    if normalized_modifier in normalized_target or normalized_target in normalized_modifier:
        return True

    compact_modifier = normalized_modifier.replace(" ", "")
    compact_target = normalized_target.replace(" ", "")
    return bool(compact_modifier and compact_target and (compact_modifier == compact_target))


def _target_text_is_replay_modifier(
    decision: ContextFrameFollowupDecision,
    text: str,
    state: OrchestratorState,
    replay_modifier: ContextFrameReplayModifier | None,
) -> bool:
    return _target_text_is_replay_modifier_for_view(
        decision,
        text,
        context_frame_state_view(state),
        replay_modifier,
    )


def _target_text_is_replay_modifier_for_view(
    decision: ContextFrameFollowupDecision,
    text: str,
    state_view: ContextFrameStateView,
    replay_modifier: ContextFrameReplayModifier | None,
) -> bool:
    target_text = _decision_target_text(decision)
    if not target_text:
        return False

    if _target_text_is_replay_amount_override(decision, text, replay_modifier):
        return True

    if _modifier_matches_target_text(_replay_source_account_candidate(text), target_text):
        return True
    if _modifier_matches_target_text(_modifier_source_account_candidate(text, replay_modifier), target_text):
        return True

    narration = _replay_narration_override(
        text,
        accounts=_loaded_accounts_for_view(state_view),
        replay_modifier=replay_modifier,
    )
    return _modifier_matches_target_text(narration, target_text)


def replay_target_entities(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    *,
    text: str,
    state: OrchestratorState,
    replay_modifier: ContextFrameReplayModifier | None,
) -> list[ContextEntity]:
    return replay_target_entities_for_view(
        frame,
        decision,
        text=text,
        state_view=context_frame_state_view(state),
        replay_modifier=replay_modifier,
    )


def replay_target_entities_for_view(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    *,
    text: str,
    state_view: ContextFrameStateView,
    replay_modifier: ContextFrameReplayModifier | None,
) -> list[ContextEntity]:
    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        if 0 <= idx < len(frame.items):
            return [frame.items[idx]]
        return []

    target_text = _decision_target_text(decision)
    if target_text:
        matches = find_matching_entities(frame, target_text)
        if matches or not _target_text_is_replay_modifier_for_view(decision, text, state_view, replay_modifier):
            return matches

    filtered = find_filtered_entities(frame, decision.filters)
    if filtered:
        return filtered

    return list(frame.items)


__all__ = ["replay_target_entities", "replay_target_entities_for_view"]
