from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.cancellation import cancel_router_fallback_reason
from apps.chat.src.agent.orchestrator.guardrails.interrupt_shortcuts import (
    resolve_interrupt_shortcut_with_reason,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.auth.auth_resolve import _approve_auth_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _cancel_updates, logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import _continue_flow_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_reprompt import _reprompt_or_reset_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.questions.active_flow_questions import (
    active_flow_question_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_core import (
    _route_interrupt,
    _shortcut_miss_category,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_switch import (
    _handle_switch_intent_route,
    _is_same_flow_transactional_switch,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices


async def _handle_auth_interrupt(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    task_planner: Any,
    redis_client: Any,
    active_type: str,
    current_task_types: set[str],
    services: OrchestrationServices,
) -> dict[str, Any]:
    shortcut_locale = interrupt_state_view(state).shortcut_locale
    shortcut_route, miss_reason = resolve_interrupt_shortcut_with_reason(
        text=text,
        interrupt_kind=interrupt.kind,
        locale=shortcut_locale,
    )

    if shortcut_route is not None:
        logger.info(
            "interrupt_auth_shortcut_ignored",
            decision=shortcut_route.decision,
            locale=shortcut_locale.value if shortcut_locale else None,
        )
    else:
        logger.info(
            "interrupt_shortcut_miss",
            kind=interrupt.kind,
            locale=shortcut_locale.value if shortcut_locale else None,
            reason=miss_reason,
            miss_category=_shortcut_miss_category(miss_reason),
        )
        logger.info(
            "interrupt_cancel_router_fallback",
            kind=interrupt.kind,
            reason=cancel_router_fallback_reason(text),
        )

    route = await _route_interrupt(
        task_planner=task_planner,
        state=state,
        text=text,
        kind=interrupt.kind,
        task_ids=interrupt.task_ids,
        current_task_types=current_task_types,
        fields_by_task=interrupt.fields_by_task,
        prompt=interrupt.prompt,
    )

    if route.decision == "active_flow_question":
        return active_flow_question_updates(
            state=state,
            interrupt=interrupt,
            route=route,
            current_task_types=current_task_types,
            semantic_path_shape="interrupt_router_only",
        )

    if route.decision in {"cancel", "reject_flow"}:
        return await _cancel_updates(state, interrupt, redis_client)

    if route.decision == "switch_intent":
        if _is_same_flow_transactional_switch(route=route, interrupt=interrupt, active_type=active_type):
            logger.info(
                "interrupt_same_flow_switch_shortcut",
                kind=interrupt.kind,
                active_type=active_type,
                target_intent=route.target_intent,
            )
            return _continue_flow_updates(state, interrupt)
        return await _handle_switch_intent_route(
            state=state,
            interrupt=interrupt,
            route=route,
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            current_task_types=current_task_types,
            services=services,
        )

    # Auth approval is callback-only for PIN. Non-PIN auth (e.g., OTP) can
    # still advance via explicit approve_flow from router classification.
    if route.decision == "approve_flow" and (interrupt.auth_method or "").lower() != "pin":
        return _approve_auth_updates(state, interrupt)

    # For PIN auth, free text cannot advance authorization.
    return await _reprompt_or_reset_updates(state, interrupt, redis_client)


__all__ = ["_handle_auth_interrupt"]
