from dataclasses import dataclass
from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_flow_hinting import (
    _has_transaction_intent_hint,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_flow_mode_decisions import (
    _forced_domain_owner,
    _is_narrow_transfer_replan,
    _should_use_compact_transaction_context,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_query_session import (
    _load_query_session_snapshot,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import (
    TRANSACTION_EXECUTORS,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_focus import (
    _infer_recent_domain_focus,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_summary_focus import _derive_recent_answer_focus
from shared.types.planner import TransactionExecutor
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PlannerContextFlowState:
    query_session_snapshot: dict[str, Any] | None
    query_session_source: str | None
    query_session_active: bool
    active_intent: str | None
    is_transactional_flow: bool
    recent_domain_focus: str | None
    recent_answer_focus: str | None
    has_transaction_intent_hint: bool
    expected_executors: tuple[TransactionExecutor, ...]
    forced_domain_owner: str | None
    compact_transaction_context: bool


def _current_flow_type(state: OrchestratorState) -> str | None:
    if not (state.waves and state.current_wave_index < len(state.waves)):
        return None
    current_wave = state.waves[state.current_wave_index]
    if not current_wave:
        return None
    wave_task = state.tasks.get(current_wave[0])
    return wave_task.type if wave_task else None


def _active_intent(state: OrchestratorState) -> str | None:
    if not state.waves:
        return None
    try:
        current_wave = state.waves[state.current_wave_index]
        if current_wave:
            task_id = current_wave[0]
            if task_id in state.tasks:
                return state.tasks[task_id].type
    except Exception as exc:
        logger.warning("active_flow_context_failed", error=str(exc))
    return None


async def build_context_flow_state(
    *,
    state: OrchestratorState,
    text: str,
    redis_client: Any | None,
) -> PlannerContextFlowState:
    current_flow_type = _current_flow_type(state)
    is_transactional_flow = current_flow_type in TRANSACTION_EXECUTORS

    query_session_snapshot, query_session_source = await _load_query_session_snapshot(state, redis_client)
    query_session_active = False
    if query_session_snapshot and not is_transactional_flow:
        query_session_active = bool(query_session_snapshot.get("session_active"))
    elif query_session_snapshot and is_transactional_flow:
        logger.info("planner_query_context_skipped", reason="active_transaction_flow")

    active_intent = _active_intent(state)
    recent_domain_focus = _infer_recent_domain_focus(state)
    recent_answer_focus = _derive_recent_answer_focus(state)
    has_transaction_intent_hint = _has_transaction_intent_hint(text)
    expected_executors = tuple(
        cast(TransactionExecutor, item)
        for item in state.preplanner_expected_transaction_executors
        if item in TRANSACTION_EXECUTORS
    )
    forced_domain_owner = _forced_domain_owner(
        state,
        active_intent=active_intent,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    )
    narrow_transfer_replan = _is_narrow_transfer_replan(
        state=state,
        active_intent=active_intent,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    )
    compact_transaction_context = narrow_transfer_replan or _should_use_compact_transaction_context(
        state=state,
        active_intent=active_intent,
        forced_domain_owner=forced_domain_owner,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    )
    return PlannerContextFlowState(
        query_session_snapshot=query_session_snapshot,
        query_session_source=query_session_source,
        query_session_active=query_session_active,
        active_intent=active_intent,
        is_transactional_flow=is_transactional_flow,
        recent_domain_focus=recent_domain_focus,
        recent_answer_focus=recent_answer_focus,
        has_transaction_intent_hint=has_transaction_intent_hint,
        expected_executors=expected_executors,
        forced_domain_owner=forced_domain_owner,
        compact_transaction_context=compact_transaction_context,
    )


__all__ = ["PlannerContextFlowState", "build_context_flow_state"]
