from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.auth.auth_flow import _handle_auth_interrupt
from apps.chat.src.agent.orchestrator.workflows.interrupt.batch_slot_fill import (
    _resolve_batch_slot_fill_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.deterministic.runner_deterministic_additive import (
    _deterministic_additive_transaction_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.deterministic.runner_deterministic_cancel import (
    _deterministic_cancel_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.deterministic.runner_deterministic_confirmation import (
    _confirmation_repeat_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.deterministic.runner_deterministic_fresh_command import (
    _fresh_command_switch_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.deterministic.runner_deterministic_input import (
    _input_greeting_updates,
    _input_shortcut_updates,
    _suggested_funding_acceptance_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.deterministic.runner_deterministic_query import (
    _standalone_query_switch_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.deterministic.runner_deterministic_status import (
    _account_balance_switch_updates,
    _deterministic_status_query_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.expiry.expiry_updates import (
    _expired_transaction_interrupt_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.engine.pending_action_semantic import (
    _resolve_semantic_pending_action_edit_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.runner_callbacks import _verified_pin_callback_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from apps.chat.src.agent.orchestrator.workflows.interrupt.schedule_read import (
    _resolve_schedule_read_during_pending_confirmation,
)


async def _resolve_pre_router_interrupt_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    expired_updates = await _expired_transaction_interrupt_updates(
        state=state,
        interrupt=runtime.interrupt,
        current_task_types=runtime.current_task_types,
        redis_client=runtime.redis_client,
        task_planner=runtime.task_planner,
        text=runtime.text,
    )
    if expired_updates is not None:
        return expired_updates

    verified_callback_updates = await _verified_pin_callback_updates(state=state, runtime=runtime)
    if verified_callback_updates is not None:
        return verified_callback_updates

    status_updates = await _deterministic_status_query_updates(state=state, runtime=runtime)
    if status_updates is not None:
        return status_updates



    account_switch_updates = await _account_balance_switch_updates(state=state, runtime=runtime)
    if account_switch_updates is not None:
        return account_switch_updates

    fresh_command_updates = await _fresh_command_switch_updates(state=state, runtime=runtime)
    if fresh_command_updates is not None:
        return fresh_command_updates

    standalone_query_updates = await _standalone_query_switch_updates(state=state, runtime=runtime)
    if standalone_query_updates is not None:
        return standalone_query_updates

    suggested_funding_updates = await _suggested_funding_acceptance_updates(state=state, runtime=runtime)
    if suggested_funding_updates is not None:
        return suggested_funding_updates

    batch_slot_updates = await _resolve_batch_slot_fill_updates(state=state, runtime=runtime)
    if batch_slot_updates is not None:
        return batch_slot_updates

    input_shortcut_updates = await _input_shortcut_updates(state=state, runtime=runtime)
    if input_shortcut_updates is not None:
        return input_shortcut_updates

    input_greeting_updates = await _input_greeting_updates(state=state, runtime=runtime)
    if input_greeting_updates is not None:
        return input_greeting_updates

    repeat_updates = _confirmation_repeat_updates(state=state, runtime=runtime)
    if repeat_updates is not None:
        return repeat_updates

    schedule_read_updates = await _resolve_schedule_read_during_pending_confirmation(
        state=state,
        interrupt=runtime.interrupt,
        text=runtime.text,
        task_planner=runtime.task_planner,
        current_task_types=runtime.current_task_types,
    )
    if schedule_read_updates is not None:
        return schedule_read_updates

    additive_updates = _deterministic_additive_transaction_updates(state=state, runtime=runtime)
    if additive_updates is not None:
        return additive_updates

    semantic_edit_updates = await _resolve_semantic_pending_action_edit_updates(
        state=state,
        interrupt=runtime.interrupt,
        text=runtime.text,
        task_planner=runtime.task_planner,
        active_type=runtime.active_type,
        current_task_types=runtime.current_task_types,
        services=runtime.services,
        redis_client=runtime.redis_client,
    )
    if semantic_edit_updates is not None:
        return semantic_edit_updates

    cancel_updates = await _deterministic_cancel_updates(state=state, runtime=runtime)
    if cancel_updates is not None:
        return cancel_updates

    if runtime.interrupt.kind == "auth":
        return await _handle_auth_interrupt(
            state=state,
            interrupt=runtime.interrupt,
            text=runtime.text,
            task_planner=runtime.task_planner,
            redis_client=runtime.redis_client,
            active_type=runtime.active_type,
            current_task_types=runtime.current_task_types,
            services=runtime.services,
        )

    return None


__all__ = ["_resolve_pre_router_interrupt_updates"]
