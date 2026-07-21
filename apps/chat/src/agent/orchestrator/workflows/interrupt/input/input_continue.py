"""State updates for continuing an interrupted input or confirmation flow."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.task_state import reset_tasks_to_extracted
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import wave_position
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_selection import (
    _select_confirmation_continue_flow_task_ids,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_updates import (
    _stash_previous_confirmation_snapshots,
    _synth_confirmation_followup_message,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view


def _ordered_reset_task_ids(
    *,
    state: OrchestratorState,
    interrupt_task_ids: list[str],
    payload_override_task_ids: list[str],
) -> list[str]:
    requested = [*interrupt_task_ids, *payload_override_task_ids]
    if not requested:
        return []

    requested_set = set(requested)
    seen: set[str] = set()
    ordered: list[str] = []
    current_wave = wave_position(state).current_wave
    tasks = interrupt_state_view(state).tasks
    for task_id in [*current_wave, *requested]:
        if task_id in requested_set and task_id in tasks and task_id not in seen:
            ordered.append(task_id)
            seen.add(task_id)
    return ordered


def _continue_flow_updates(
    state: OrchestratorState,
    interrupt: Any,
    precomputed_payload_overrides: dict[str, dict[str, Any]] | None = None,
    input_messages_by_task: dict[str, str] | None = None,
) -> dict[str, Any]:
    state_view = interrupt_state_view(state)
    if interrupt.kind in {"input", "confirmation"}:
        original_task_ids = [str(task_id) for task_id in interrupt.task_ids]
        payload_overrides: dict[str, dict[str, Any]] = dict(precomputed_payload_overrides or {})
        task_ids_to_reset = _ordered_reset_task_ids(
            state=state,
            interrupt_task_ids=original_task_ids,
            payload_override_task_ids=list(payload_overrides),
        )
        selection_reason = "input_flow"
        matched_task_ids: list[str] = []
        message_overrides: dict[str, str] = {}
        if interrupt.kind == "confirmation":
            task_ids_to_reset, selection_reason, matched_task_ids = _select_confirmation_continue_flow_task_ids(
                state,
                interrupt,
            )
            if payload_overrides:
                task_ids_to_reset = _ordered_reset_task_ids(
                    state=state,
                    interrupt_task_ids=[],
                    payload_override_task_ids=list(payload_overrides),
                )
                selection_reason = "precomputed_correction_scope"
                matched_task_ids = task_ids_to_reset
        logger.info(
            "confirmation_update_detected",
            tasks=interrupt.task_ids,
            reset_task_ids=task_ids_to_reset,
            selection_reason=selection_reason,
            matched_task_ids=matched_task_ids,
        )
        if interrupt.kind == "confirmation":
            _stash_previous_confirmation_snapshots(state, task_ids_to_reset)
        reset_tasks_to_extracted(
            state_view.tasks,
            task_ids_to_reset,
            copy_task=True,
            clear_idempotency=True,
        )
        if interrupt.kind == "confirmation":
            if payload_overrides:
                logger.info(
                    "confirmation_task_payload_overrides_applied",
                    task_ids=sorted(payload_overrides.keys()),
                )
            if message_overrides:
                logger.info(
                    "confirmation_task_message_overrides_applied",
                    task_ids=sorted(message_overrides.keys()),
                )
            for task_id in task_ids_to_reset:
                task = state_view.task(task_id)
                if task is None:
                    continue
                if task_id in payload_overrides:
                    task.payload.update(payload_overrides[task_id])
                if task_id in message_overrides:
                    task.payload["pending_user_message"] = message_overrides[task_id]
                    task.payload["confirmation_message_scoped"] = True
                elif task_id in payload_overrides:
                    synthesized = _synth_confirmation_followup_message(task)
                    if synthesized:
                        task.payload["pending_user_message"] = synthesized
                        task.payload["confirmation_message_scoped"] = True
                    else:
                        task.payload.pop("pending_user_message", None)
                        task.payload.pop("confirmation_message_scoped", None)
                else:
                    task.payload.pop("pending_user_message", None)
                    task.payload.pop("confirmation_message_scoped", None)
        elif interrupt.kind == "input" and payload_overrides:
            logger.info(
                "input_task_payload_overrides_applied",
                task_ids=sorted(payload_overrides.keys()),
            )
            for task_id in task_ids_to_reset:
                task = state_view.task(task_id)
                if task is None or task_id not in payload_overrides:
                    continue
                task.payload.update(payload_overrides[task_id])
                task.payload.pop("pending_user_message", None)
                task.payload.pop("confirmation_message_scoped", None)
        if interrupt.kind == "input" and input_messages_by_task is not None:
            for task_id in task_ids_to_reset:
                task = state_view.task(task_id)
                if task is None:
                    continue
                scoped_message = input_messages_by_task.get(task_id)
                if scoped_message:
                    task.payload["pending_user_message"] = scoped_message
                    task.payload.pop("suppress_current_input", None)
                else:
                    # A reply for one slot (for example option "1") must not
                    # be replayed into another incomplete batch task.
                    task.payload.pop("pending_user_message", None)
                    task.payload["suppress_current_input"] = True
            logger.info(
                "batch_input_reply_scoped",
                target_task_count=len(input_messages_by_task),
                deferred_task_count=max(len(task_ids_to_reset) - len(input_messages_by_task), 0),
            )
        last_interrupt = interrupt
        if interrupt.kind == "confirmation" and task_ids_to_reset != [str(task_id) for task_id in interrupt.task_ids]:
            if hasattr(interrupt, "model_copy"):
                last_interrupt = interrupt.model_copy(update={"task_ids": task_ids_to_reset})
        return {
            "pending_interrupt": None,
            "last_interrupt": last_interrupt,
            "tasks": state_view.tasks,
            "pin_verified": False,
            "authorization_context": None,
            "last_callback": None,
        }
    return {}


__all__ = ["_continue_flow_updates"]
