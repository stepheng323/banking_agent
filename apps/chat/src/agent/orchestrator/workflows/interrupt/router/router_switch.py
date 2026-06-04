from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_beneficiary import (
    _is_beneficiary_clarification_interrupt,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_semantic import _route_interrupt_semantic_turn
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    KNOWN_SWITCH_INTENTS,
    NON_TRANSACTION_SWITCH_INTENTS,
    TRANSACTION_INTENTS,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_tasks import (
    _build_direct_non_transaction_switch_tasks,
    _build_enriched_transaction_switch_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_updates import (
    _build_planner_switch_updates,
    _switch_updates,
)
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from shared.types.planner import InterruptRouteDecision


def _is_transaction_intent(intent: str | None) -> bool:
    return bool(intent and intent in TRANSACTION_INTENTS)


def _is_same_flow_transactional_switch(
    *,
    route: InterruptRouteDecision,
    interrupt: Any,
    active_type: str,
) -> bool:
    if route.decision != "switch_intent":
        return False
    if interrupt.kind != "confirmation":
        return False
    if active_type not in TRANSACTION_INTENTS:
        return False
    return str(route.target_intent or "").strip().lower() == active_type


async def _handle_switch_intent_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    route: InterruptRouteDecision,
    task_planner: Any,
    text: str,
    active_type: str,
    current_task_types: set[str],
    services: OrchestrationServices,
) -> dict[str, Any]:
    from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_flow import _reprompt_updates

    if _is_beneficiary_clarification_interrupt(interrupt):
        logger.info(
            "interrupt_switch_blocked",
            reason="beneficiary_disambiguation_pending",
            target_intent=route.target_intent,
            tasks=interrupt.task_ids,
        )
        return _reprompt_updates(state, interrupt)

    target_intent = (route.target_intent or "").strip().lower()
    if not target_intent or target_intent not in KNOWN_SWITCH_INTENTS:
        logger.info(
            "interrupt_switch_target_unknown",
            target_intent=target_intent or None,
            reason="missing_or_unsupported_target",
        )
        return _reprompt_updates(state, interrupt)

    if target_intent in NON_TRANSACTION_SWITCH_INTENTS:
        new_tasks, waves, new_task_types = _build_direct_non_transaction_switch_tasks(
            state=state,
            text=text,
            target_intent=target_intent,
            route=route,
        )
    else:
        semantic_route = await _route_interrupt_semantic_turn(
            task_planner=task_planner,
            state=state,
            text=text,
            add_task_instruction_only=route.target_mode == "continuation",
        )

        semantic_decision = str(getattr(semantic_route, "decision", "") or "")
        expected_executors = [
            str(item)
            for item in (getattr(semantic_route, "expected_transaction_executors", None) or [])
            if str(item) in TRANSACTION_INTENTS
        ]
        direct_transaction_domains = {
            "domain_transfer": "transfer",
            "domain_airtime": "airtime",
            "domain_data": "data",
        }
        if (
            route.target_mode == "continuation"
            and target_intent in TRANSACTION_INTENTS
            and len(set(expected_executors)) == 1
        ):
            resolved_target_intent = expected_executors[0]
        elif semantic_decision in {"planner_mixed", "planner_ambiguous"}:
            return _build_planner_switch_updates(
                state=state,
                interrupt=interrupt,
                active_type=active_type,
                current_task_types=current_task_types,
                text=text,
                expected_executors=expected_executors,
            )
        else:
            resolved_target_intent = direct_transaction_domains.get(semantic_decision, target_intent)
        new_tasks, waves, new_task_types = await _build_enriched_transaction_switch_tasks(
            state=state,
            text=text,
            target_intent=resolved_target_intent,
            interrupt=interrupt,
            services=services,
        )
        target_intent = resolved_target_intent

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
        merge_with_pending_confirmation=route.target_mode == "continuation",
    )


__all__ = [
    "_handle_switch_intent_route",
    "_is_same_flow_transactional_switch",
    "_is_transaction_intent",
]
