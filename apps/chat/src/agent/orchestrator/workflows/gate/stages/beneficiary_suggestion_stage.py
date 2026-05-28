from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.beneficiary_suggestions import (
    _resolve_beneficiary_suggestion_reply,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.direct_tasks import _next_direct_beneficiary_task_id
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_beneficiary_suggestion(ctx: GateContext) -> dict[str, Any] | None:
    """Beneficiary save/dismiss from Redis suggestion."""
    if ctx.live_pending_interrupt or not ctx.redis_client:
        return None

    suggestion_key = f"user:{ctx.state.phone_number}:beneficiary_suggestion"
    try:
        suggestion_data = await ctx.redis_client.get(suggestion_key)
    except Exception as exc:
        logger.warning("beneficiary_suggestion_lookup_failed", error=str(exc))
        suggestion_data = None

    if not suggestion_data:
        return None

    decision = _resolve_beneficiary_suggestion_reply(
        ctx.message_text,
        locale=ctx.current_locale,
    )
    logger.info(
        "beneficiary_suggestion_gate_decision",
        decision=decision.action,
        reason=decision.reason,
        locale=ctx.current_locale,
        alias_present=bool(decision.alias),
    )
    if decision.action in {"save_default", "save_alias"}:
        task_id = _next_direct_beneficiary_task_id(ctx.state.tasks)
        task_payload: dict[str, Any] = {
            "action": "save_beneficiary",
            "instruction": ctx.state.last_message_text,
            "message": ctx.state.last_message_text,
        }
        if decision.alias:
            task_payload["alias"] = decision.alias
        spec = TaskSpec(
            id=task_id,
            type="beneficiary",
            stage=TaskStage.DRAFT,
            payload=task_payload,
        )
        return {
            **ctx.gate_updates,
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "pending_interrupt": None,
            "direct_path_triggered": True,
            **_route_observability_updates(
                owner="guardrail",
                decision="beneficiary_save",
                target_domain="beneficiary",
                mode="new",
                route_source="beneficiary_suggestion",
                heuristic_type="guardrail_shortcut",
                heuristic_name="beneficiary_suggestion_reply",
            ),
        }

    try:
        await ctx.redis_client.delete(suggestion_key)
    except Exception as exc:
        logger.warning("beneficiary_suggestion_dismiss_delete_failed", error=str(exc))
    else:
        logger.info("beneficiary_suggestion_dismissed", reason=decision.reason)
    return None
