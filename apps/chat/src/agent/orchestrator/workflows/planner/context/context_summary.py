"""Turn context summary construction for planner, gate, and interrupt flows."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.referents.store import build_referent_memory_summary
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_query_session import (
    _load_query_session_snapshot,
    _query_session_summary_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_rendering_user import (
    build_user_state_summary_from_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_summary_active_flow import (
    _build_active_flow_details,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_summary_focus import (
    _derive_recent_answer_focus,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_summary_payload import (
    CONTEXT_ACCOUNT_PREVIEW_LIMIT,
    _build_account_lines,
    _build_beneficiary_lines,
    _build_history_lines,
    _compact_payload_for_prompt,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_summary_state import (
    _get_or_build_turn_context_summary,
    summary_from_state_payload,
    summary_to_state_payload,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_types import TurnContextSummary
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import planner_state_view
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def build_turn_context_summary(
    state: OrchestratorState,
    *,
    query_session_snapshot: dict[str, Any] | None = None,
    query_session_source: str | None = None,
) -> TurnContextSummary:
    state_view = planner_state_view(state)
    ctx = state_view.loaded_context_or_empty
    profile = ctx.get("profile") or {}
    accounts = ctx.get("accounts") or []
    beneficiaries = ctx.get("beneficiaries") or []
    history = ctx.get("history") or []

    profile_name = None
    if isinstance(profile, dict) and profile.get("first_name"):
        profile_name = f"{profile.get('first_name')} {profile.get('last_name') or ''}".strip()

    account_lines, remaining_accounts = _build_account_lines(accounts if isinstance(accounts, list) else [])
    beneficiary_lines, remaining_beneficiaries = _build_beneficiary_lines(
        beneficiaries if isinstance(beneficiaries, list) else []
    )
    history_lines = _build_history_lines(history if isinstance(history, list) else [])
    query_session_summary, query_session_active = _query_session_summary_text(query_session_snapshot)
    active_flow = _build_active_flow_details(state_view)

    from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
    from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_focus import (
        _infer_recent_domain_focus,
    )

    return TurnContextSummary(
        active_domain=state_view.active_domain,
        session_domain=state_view.session_domain,
        recent_domain_focus=_infer_recent_domain_focus(state_view),
        recent_answer_focus=_derive_recent_answer_focus(state_view),
        profile_name=profile_name,
        account_lines=account_lines,
        remaining_accounts=remaining_accounts,
        beneficiary_lines=beneficiary_lines,
        remaining_beneficiaries=remaining_beneficiaries,
        history_lines=history_lines,
        query_session_summary=query_session_summary,
        query_session_active=query_session_active,
        query_session_source=query_session_source,
        active_flow_summary=active_flow.summary,
        active_flow_intent=active_flow.intent,
        active_flow_missing_fields=active_flow.missing_fields,
        active_flow_interrupt_kind=active_flow.interrupt_kind,
        short_term_memory_summary=OrchestratorContextManager().build_llm_summary(state) or None,
        referent_memory_summary=build_referent_memory_summary(state) or None,
    )


def get_or_build_turn_context_summary(
    state: OrchestratorState,
    *,
    query_session_snapshot: dict[str, Any] | None = None,
    query_session_source: str | None = None,
    path_label: str | None = None,
    builder: Any | None = None,
    perf_logger: Any | None = None,
) -> tuple[TurnContextSummary, dict[str, Any] | None]:
    state_view = planner_state_view(state)
    return _get_or_build_turn_context_summary(
        state_view,
        query_session_snapshot=query_session_snapshot,
        query_session_source=query_session_source,
        path_label=path_label,
        builder=builder or build_turn_context_summary,
        perf_logger=perf_logger or logger,
    )


def _build_user_state_summary(state: OrchestratorState) -> str | None:
    """Build a compact, human-readable summary of the user's persistent state."""
    summary, _ = get_or_build_turn_context_summary(state)
    return build_user_state_summary_from_summary(summary)


__all__ = [
    "CONTEXT_ACCOUNT_PREVIEW_LIMIT",
    "_build_user_state_summary",
    "_compact_payload_for_prompt",
    "_derive_recent_answer_focus",
    "_load_query_session_snapshot",
    "build_turn_context_summary",
    "get_or_build_turn_context_summary",
    "summary_from_state_payload",
    "summary_to_state_payload",
]
