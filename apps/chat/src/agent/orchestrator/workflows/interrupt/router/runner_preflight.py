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
from apps.chat.src.agent.orchestrator.workflows.interrupt.outcome import (
    InterruptResolution,
    resolve_interrupt_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.engine.pending_action_semantic import (
    _resolve_semantic_pending_action_edit_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.runner_callbacks import _verified_pin_callback_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from apps.chat.src.agent.orchestrator.workflows.interrupt.schedule_read import (
    _resolve_schedule_read_during_pending_confirmation,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.single_transfer_edit import (
    resolve_single_transfer_confirmation_edit_updates,
)


async def _resolve_pre_router_interrupt_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> InterruptResolution | None:
    expired_updates = await _expired_transaction_interrupt_updates(
        state=state,
        interrupt=runtime.interrupt,
        current_task_types=runtime.current_task_types,
        redis_client=runtime.redis_client,
        task_planner=runtime.task_planner,
        text=runtime.text,
    )
    if expired_updates is not None:
        return resolve_interrupt_updates(state, expired_updates, decision="interrupt_expiry")

    verified_callback_updates = await _verified_pin_callback_updates(state=state, runtime=runtime)
    if verified_callback_updates is not None:
        return resolve_interrupt_updates(
            state,
            verified_callback_updates,
            decision="interrupt_verified_callback",
        )

    status_updates = await _deterministic_status_query_updates(state=state, runtime=runtime)
    if status_updates is not None:
        return resolve_interrupt_updates(state, status_updates, decision="interrupt_status")

    account_switch_updates = await _account_balance_switch_updates(state=state, runtime=runtime)
    if account_switch_updates is not None:
        return resolve_interrupt_updates(
            state,
            account_switch_updates,
            decision="interrupt_supported_pivot",
        )

    fresh_command_updates = await _fresh_command_switch_updates(state=state, runtime=runtime)
    if fresh_command_updates is not None:
        return resolve_interrupt_updates(
            state,
            fresh_command_updates,
            decision="interrupt_supported_pivot",
        )

    standalone_query_updates = await _standalone_query_switch_updates(state=state, runtime=runtime)
    if standalone_query_updates is not None:
        return resolve_interrupt_updates(
            state,
            standalone_query_updates,
            decision="interrupt_supported_pivot",
        )

    suggested_funding_updates = await _suggested_funding_acceptance_updates(state=state, runtime=runtime)
    if suggested_funding_updates is not None:
        return resolve_interrupt_updates(
            state,
            suggested_funding_updates,
            decision="interrupt_slot_fill",
        )

    batch_slot_updates = await _resolve_batch_slot_fill_updates(state=state, runtime=runtime)
    if batch_slot_updates is not None:
        return resolve_interrupt_updates(state, batch_slot_updates, decision="interrupt_slot_fill")

    input_shortcut_updates = await _input_shortcut_updates(state=state, runtime=runtime)
    if input_shortcut_updates is not None:
        return resolve_interrupt_updates(
            state,
            input_shortcut_updates,
            decision="interrupt_slot_fill",
        )

    input_greeting_updates = await _input_greeting_updates(state=state, runtime=runtime)
    if input_greeting_updates is not None:
        return resolve_interrupt_updates(
            state,
            input_greeting_updates,
            decision="interrupt_reprompt",
        )

    repeat_updates = _confirmation_repeat_updates(state=state, runtime=runtime)
    if repeat_updates is not None:
        return resolve_interrupt_updates(state, repeat_updates, decision="interrupt_reprompt")

    schedule_read_updates = await _resolve_schedule_read_during_pending_confirmation(
        state=state,
        interrupt=runtime.interrupt,
        text=runtime.text,
        task_planner=runtime.task_planner,
        current_task_types=runtime.current_task_types,
    )
    if schedule_read_updates is not None:
        return resolve_interrupt_updates(
            state,
            schedule_read_updates,
            decision="interrupt_status",
            target_domain="schedule",
        )

    additive_updates = _deterministic_additive_transaction_updates(state=state, runtime=runtime)
    if additive_updates is not None:
        return resolve_interrupt_updates(
            state,
            additive_updates,
            decision="interrupt_supported_pivot",
        )

    cancel_updates = await _deterministic_cancel_updates(state=state, runtime=runtime)
    if cancel_updates is not None:
        return resolve_interrupt_updates(state, cancel_updates, decision="interrupt_cancelled")

    single_transfer_edit_updates = await resolve_single_transfer_confirmation_edit_updates(
        state=state,
        runtime=runtime,
    )
    if single_transfer_edit_updates is not None:
        return resolve_interrupt_updates(
            state,
            single_transfer_edit_updates,
            decision="interrupt_single_transfer_edit",
        )

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
        return resolve_interrupt_updates(
            state,
            semantic_edit_updates,
            decision="interrupt_correction",
        )

    if runtime.interrupt.kind == "auth":
        auth_updates = await _handle_auth_interrupt(
            state=state,
            interrupt=runtime.interrupt,
            text=runtime.text,
            task_planner=runtime.task_planner,
            redis_client=runtime.redis_client,
            active_type=runtime.active_type,
            current_task_types=runtime.current_task_types,
            services=runtime.services,
        )
        return resolve_interrupt_updates(state, auth_updates, decision="interrupt_auth")

    return None


__all__ = ["_resolve_pre_router_interrupt_updates"]
