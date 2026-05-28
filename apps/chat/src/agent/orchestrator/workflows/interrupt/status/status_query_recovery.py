from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _cancel_updates, logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_semantic import _route_interrupt_semantic_turn
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_tasks import (
    _build_direct_non_transaction_switch_tasks,
    _build_enriched_transaction_switch_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_updates import (
    _build_planner_switch_updates,
    _switch_updates,
)
from shared.types.planner import InterruptRouteDecision


async def _recover_status_query_without_active_flow(
    *,
    state: OrchestratorState,
    interrupt: Any,
    route: InterruptRouteDecision,
    current_task_types: set[str],
    task_planner: Any,
    text: str,
    active_type: str,
    services: dict[str, Any],
    redis_client: Any | None,
) -> dict[str, Any]:
    from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_flow import _reprompt_updates

    logger.info(
        "interrupt_status_query_no_active_flow",
        kind=interrupt.kind,
        status_query_type=route.status_query_type,
        active_types=sorted(current_task_types),
    )
    semantic_route = await _route_interrupt_semantic_turn(
        task_planner=task_planner,
        state=state,
        text=text,
    )
    semantic_decision = str(getattr(semantic_route, "decision", "") or "")
    expected_executors = [
        str(item)
        for item in (getattr(semantic_route, "expected_transaction_executors", None) or [])
        if str(item) in TRANSACTION_INTENTS
    ]
    logger.info(
        "interrupt_status_query_semantic_recovery",
        decision=semantic_decision or None,
        mode=getattr(semantic_route, "mode", None),
        target_intent=getattr(semantic_route, "target_intent", None),
        expected_executors=expected_executors,
    )

    if semantic_decision == "cancel":
        return await _cancel_updates(state, interrupt, redis_client)

    if semantic_decision in {"planner_mixed", "planner_ambiguous"}:
        return _build_planner_switch_updates(
            state=state,
            interrupt=interrupt,
            active_type=active_type,
            current_task_types=current_task_types,
            text=text,
            expected_executors=expected_executors,
        )

    direct_non_transaction_domains = {
        "domain_query": "query",
        "domain_account": "account",
        "domain_support": "support",
        "domain_beneficiary": "beneficiary",
    }
    target_intent = direct_non_transaction_domains.get(semantic_decision)
    if target_intent is not None:
        recovered_route = InterruptRouteDecision(
            decision="switch_intent",
            confidence=getattr(semantic_route, "confidence", 0.0) or 0.0,
            detected_language=getattr(semantic_route, "detected_language", None),
            target_intent=target_intent,
            target_mode="continuation" if getattr(semantic_route, "mode", None) == "continuation" else "new",
            reason="status_query_no_active_flow_semantic_recovery",
        )
        new_tasks, waves, new_task_types = _build_direct_non_transaction_switch_tasks(
            state=state,
            text=text,
            target_intent=target_intent,
            route=recovered_route,
        )
        return _switch_updates(
            state=state,
            interrupt=interrupt,
            active_type=active_type,
            current_task_types=current_task_types,
            new_tasks=new_tasks,
            waves=waves,
            new_task_types=new_task_types,
            text=text,
            planner_output=None,
            primary_intent=target_intent,
        )

    direct_transaction_domains = {
        "domain_transfer": "transfer",
        "domain_airtime": "airtime",
        "domain_data": "data",
    }
    target_intent = direct_transaction_domains.get(semantic_decision)
    if target_intent is not None:
        new_tasks, waves, new_task_types = await _build_enriched_transaction_switch_tasks(
            state=state,
            text=text,
            target_intent=target_intent,
            interrupt=interrupt,
            services=services,
        )
        return _switch_updates(
            state=state,
            interrupt=interrupt,
            active_type=active_type,
            current_task_types=current_task_types,
            new_tasks=new_tasks,
            waves=waves,
            new_task_types=new_task_types,
            text=text,
            planner_output=None,
            primary_intent=target_intent,
        )

    return _reprompt_updates(state, interrupt)


__all__ = ["_recover_status_query_without_active_flow"]
