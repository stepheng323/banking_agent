from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis

from apps.chat.src.agent.orchestrator.workflows.planner.context.flow.context_flow_hinting import (
    _has_transaction_intent_hint,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.flow.context_flow_mode_decisions import (
    _forced_domain_owner,
    _is_narrow_transfer_replan,
    _should_use_compact_transaction_context,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.query_session.context_query_session import (
    _load_query_session_snapshot,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_constants import (
    TRANSACTION_EXECUTORS,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_focus import (
    _infer_recent_domain_focus,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_summary_focus import (
    _derive_recent_answer_focus,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.types.planner import RouterDomainIntent, TransactionExecutor
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
    forced_domain_owner: RouterDomainIntent | None
    compact_transaction_context: bool


async def build_context_flow_state(
    *,
    state_view: PlannerStateView,
    text: str,
    redis_client: redis.Redis | None,
) -> PlannerContextFlowState:
    current_flow_type = state_view.current_wave_first_task_type
    is_transactional_flow = current_flow_type in TRANSACTION_EXECUTORS

    query_session_snapshot, query_session_source = await _load_query_session_snapshot(state_view)
    query_session_active = False
    if query_session_snapshot and not is_transactional_flow:
        query_session_active = bool(query_session_snapshot.get("session_active"))
    elif query_session_snapshot and is_transactional_flow:
        logger.info("planner_query_context_skipped", reason="active_transaction_flow")

    active_intent = state_view.current_wave_first_task_type
    recent_domain_focus = _infer_recent_domain_focus(state_view)
    recent_answer_focus = _derive_recent_answer_focus(state_view)
    has_transaction_intent_hint = _has_transaction_intent_hint(text)
    expected_executors = state_view.expected_transaction_executors
    forced_domain_owner = _forced_domain_owner(
        state_view,
        active_intent=active_intent,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    )
    narrow_transfer_replan = _is_narrow_transfer_replan(
        state_view=state_view,
        active_intent=active_intent,
        expected_executors=expected_executors,
        query_session_active=query_session_active,
    )
    compact_transaction_context = narrow_transfer_replan or _should_use_compact_transaction_context(
        state_view=state_view,
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
