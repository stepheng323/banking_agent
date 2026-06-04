from typing import Any

from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _is_obvious_data_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.direct_tasks import (
    _build_direct_domain_task,
    _direct_domain_capability_block_message,
)
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from apps.chat.src.agent.orchestrator.workflows.gate.stages.domain_data_plan import (
    _apply_self_data_target,
    _extract_data_purchase_hints,
    _extract_data_query_payload,
    _is_data_plan_query_request,
    _is_data_plan_reference_purchase_request,
    _resolved_data_plan_payload,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_data_plan_query(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic catalog-question shortcut for data plan prices/availability."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_data_plan_query_request(ctx.message_text)
    ):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state_view, "data"):
        logger.info("gate_data_plan_query_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_data_plan_query_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="data",
                mode="new",
                route_source="data_plan_query_guard",
                heuristic_type="slot_parser",
                heuristic_name="data_plan_query",
            ),
        }

    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="data", mode="new")
    spec.payload.clear()
    spec.payload.update(_extract_data_query_payload(ctx.message_text))
    logger.info("gate_deterministic_data_plan_query", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_data_plan_query",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_data_plan_query",
            target_domain="data",
            mode="new",
            route_source="data_plan_query_guard",
            heuristic_type="slot_parser",
            heuristic_name="data_plan_query",
        ),
    }


async def _stage_data_plan_reference_purchase(ctx: GateContext) -> dict[str, Any] | None:
    """Route "buy it" after a data-plan answer into a normal data purchase."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or not _is_data_plan_reference_purchase_request(ctx.message_text)
    ):
        return None
    data = _resolved_data_plan_payload(ctx)
    if not data:
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state_view, "data"):
        logger.info("gate_data_plan_reference_purchase_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "data_plan_reference_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="data",
                mode="new",
                route_source="data_plan_referent",
                heuristic_type="referent_memory",
                heuristic_name="data_plan_reference",
            ),
        }

    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="data", mode="new")
    spec.payload.update(
        {
            "action": "buy_data",
            "plan_code": data.get("plan_code") or data.get("item_code"),
            "plan_name": data.get("plan_name") or data.get("name"),
            "biller_code": data.get("biller_code"),
            "plan_size_gb": data.get("size_gb"),
            "plan_validity_days": data.get("validity_days"),
            "plan_tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
            "network": data.get("network"),
            "amount": data.get("amount"),
            "skip_extraction": True,
        }
    )
    _apply_self_data_target(spec.payload, text=ctx.message_text, phone_number=ctx.state_view.phone_number)
    logger.info("gate_data_plan_reference_purchase", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "data_plan_reference_purchase",
        **_route_observability_updates(
            owner="guardrail",
            decision="data_plan_reference_purchase",
            target_domain="data",
            mode="new",
            route_source="data_plan_referent",
            heuristic_type="referent_memory",
            heuristic_name="data_plan_reference",
        ),
    }


async def _stage_data_domain(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic data domain shortcut."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_quote
        or not ctx.phrase_heavy_fastpath_allowed
        or not _is_obvious_data_request(ctx.message_text)
    ):
        return None
    if block_message := _direct_domain_capability_block_message(ctx.state_view, "data"):
        logger.info("gate_deterministic_data_domain_policy_blocked")
        return {
            **ctx.gate_updates,
            "final_response": block_message,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_data_domain_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="capability_blocked",
                target_domain="data",
                mode="new",
                route_source="data_domain_guard",
                heuristic_type="slot_parser",
                heuristic_name="obvious_data_request",
            ),
        }
    task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="data", mode="new")
    spec.payload.update(_extract_data_purchase_hints(ctx.message_text, phone_number=ctx.state_view.phone_number))
    logger.info("gate_deterministic_data_domain", task_id=task_id)
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "deterministic_data_domain",
        **_route_observability_updates(
            owner="guardrail",
            decision="deterministic_data_domain",
            target_domain="data",
            mode="new",
            route_source="data_domain_guard",
            heuristic_type="slot_parser",
            heuristic_name="obvious_data_request",
        ),
    }
