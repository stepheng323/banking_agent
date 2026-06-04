from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.cancellation import clear_query_session
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.mixed_capabilities import (
    SupportedClause,
    analyze_mixed_supported_unsupported,
    analyze_mixed_supported_unsupported_semantic,
    mixed_clarify_params,
    mixed_policy_notice,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
    _next_direct_account_task_id,
)
from apps.chat.src.agent.orchestrator.workflows.gate.query_session_exit import _build_query_session_exit_updates
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from apps.chat.src.agent.orchestrator.workflows.gate.state_view import GateStateView
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _build_supported_task(state_view: GateStateView, supported: SupportedClause) -> tuple[str, TaskSpec]:
    if supported.domain == "account" and supported.heuristic_name == "balance_request":
        task_id = _next_direct_account_task_id(state_view.tasks)
        spec = TaskSpec(
            id=task_id,
            type="account",
            stage=TaskStage.DRAFT,
            payload={
                "action": "check_balance",
                "message": supported.text,
                "instruction": supported.text,
            },
        )
        return task_id, spec
    if supported.domain == "schedule":
        return _build_direct_domain_task(
            state_view=state_view,
            domain="schedule",
            mode="new",
            schedule_response_mode="list",
            message_text=supported.text,
        )
    return _build_direct_domain_task(
        state_view=state_view,
        domain=supported.domain,
        mode="new",
        message_text=supported.text,
    )


async def _stage_mixed_supported_unsupported_capability(ctx: GateContext) -> dict[str, Any] | None:
    """Route one supported banking clause while refusing unsupported clauses."""
    if ctx.live_pending_interrupt or ctx.state_view.has_gate_blocking_state or not ctx.phrase_heavy_fastpath_allowed:
        return None

    match = analyze_mixed_supported_unsupported(ctx.message_text)
    if match is None:
        match = await analyze_mixed_supported_unsupported_semantic(
            text=ctx.message_text,
            locale=ctx.current_locale,
            task_planner=ctx.task_planner,
        )
    if match is None:
        return None

    notice = mixed_policy_notice(match, locale=ctx.current_locale)
    if match.is_ambiguous:
        logger.info(
            "gate_mixed_capability_ambiguous",
            supported_count=len(match.supported),
            unsupported=[item.key for item in match.unsupported],
        )
        return {
            **ctx.gate_updates,
            "capability_boundary": None,
            "direct_path_triggered": True,
            "final_response": render_message(
                "orchestrator.ambiguity.mixed_supported_unsupported",
                ctx.current_locale,
                mixed_clarify_params(match, locale=ctx.current_locale),
            ),
            "semantic_path_shape": "mixed_capability_clarify",
            **_route_observability_updates(
                owner="guardrail",
                decision="mixed_supported_unsupported_clarify",
            ),
        }

    supported = match.supported[0]
    if block_message := _direct_domain_capability_block_message(ctx.state_view, supported.domain):
        return {
            **ctx.gate_updates,
            "capability_boundary": None,
            "direct_path_triggered": True,
            "final_response": f"{notice}\n\n{block_message}",
            "semantic_path_shape": "mixed_capability_supported_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="mixed_supported_unsupported_policy_blocked",
                target_domain=supported.domain,
                mode="new",
            ),
        }

    task_id, spec = _build_supported_task(ctx.state_view, supported)
    task_updates: dict[str, Any] = {}
    if supported.domain == "transfer":
        await ctx.ensure_query_session()
        if (
            ctx.redis_client
            and isinstance(ctx.query_session_snapshot, dict)
            and ctx.query_session_snapshot.get("session_active")
        ):
            await clear_query_session(ctx.redis_client, ctx.state_view.phone_number)
            task_updates.update(
                _build_query_session_exit_updates(
                    ctx.state,
                    query_session_snapshot=ctx.query_session_snapshot,
                )
            )
    if supported.domain == "transfer" and supported.heuristic_name == "recipient_bank_details_only":
        spec.payload["amount_suggestion_disabled"] = True

    logger.info(
        "gate_mixed_capability_supported_direct",
        supported_domain=supported.domain,
        unsupported=[item.key for item in match.unsupported],
    )
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        **task_updates,
        "capability_boundary": None,
        "policy_notice": notice,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "mixed_capability_supported_direct",
        **_route_observability_updates(
            owner="guardrail",
            decision="mixed_supported_unsupported",
            target_domain=supported.domain,
            mode="new",
            route_source="mixed_capability_guard",
            heuristic_type="clause_splitter",
            heuristic_name=supported.heuristic_name,
        ),
    }
