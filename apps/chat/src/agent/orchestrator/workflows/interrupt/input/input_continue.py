"""State updates for continuing an interrupted input or confirmation flow."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.task_payload_recipients import (
    clear_external_recipient_bindings_for_self,
)
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

_TERMINAL_TASK_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}
_TRANSACTION_TASK_TYPES = {"transfer", "airtime", "data"}


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


def _active_batch_scope_task_ids(
    *,
    state: OrchestratorState,
    interrupt: Any,
) -> list[str]:
    """Return every live transaction task belonging to an input batch.

    New focused-input prompts explicitly persist ``batch_task_ids``.  The
    async-group fallback covers a checkpoint created by an older prompt path
    or a planner wave that was split before the selection reply arrived.
    """
    state_view = interrupt_state_view(state)
    tasks = state_view.tasks
    metadata = getattr(interrupt, "metadata", None)
    raw_scope = metadata.get("batch_task_ids") if isinstance(metadata, dict) else None

    requested = [str(task_id) for task_id in raw_scope] if isinstance(raw_scope, list) else []
    live_scope = [
        task_id
        for task_id in requested
        if task_id in tasks and tasks[task_id].stage not in _TERMINAL_TASK_STAGES
    ]
    if len(live_scope) > 1:
        return list(dict.fromkeys(live_scope))

    group_ids = {
        str(task.payload.get("async_group_id"))
        for task_id in getattr(interrupt, "task_ids", []) or []
        if (task := tasks.get(str(task_id))) is not None
        and task.type in _TRANSACTION_TASK_TYPES
        and task.stage not in _TERMINAL_TASK_STAGES
        and task.payload.get("async_group_id")
    }
    if not group_ids:
        return live_scope

    # Preserve the planned/wave order for the eventual combined review.
    ordered_ids = [task_id for wave in state.waves for task_id in wave]
    ordered_ids.extend(task_id for task_id in tasks if task_id not in ordered_ids)
    return [
        task_id
        for task_id in ordered_ids
        if (task := tasks.get(task_id)) is not None
        and task.type in _TRANSACTION_TASK_TYPES
        and task.stage not in _TERMINAL_TASK_STAGES
        and str(task.payload.get("async_group_id") or "") in group_ids
    ]


def _merge_batch_scope_into_current_wave(
    *,
    state: OrchestratorState,
    batch_task_ids: list[str],
) -> list[list[str]] | None:
    """Reunite a persisted transaction batch before resuming its input.

    A previous planner/checkpoint can leave one leg in a later wave.  Resetting
    that leg alone is not enough: execution would then confirm the focused
    leg and stop before it reaches the sibling.  A shared review requires all
    live batch legs to be evaluated in the same wave.
    """
    if len(batch_task_ids) < 2:
        return None

    position = wave_position(state)
    if not position.has_current_wave:
        return None

    current_index = position.index
    scope = list(dict.fromkeys(batch_task_ids))
    scope_set = set(scope)
    current_wave = list(position.current_wave)
    if not scope_set.intersection(current_wave):
        return None

    later_contains_scope = any(
        scope_set.intersection(wave) for wave in position.waves[current_index + 1 :]
    )
    if not later_contains_scope:
        return None

    waves = [list(wave) for wave in position.waves]
    # The batch scope is already ordered from the planner/current prompt.  It
    # must come before unrelated work in this wave so it gets one atomic
    # input/review/funding lifecycle.
    waves[current_index] = [*scope, *[task_id for task_id in current_wave if task_id not in scope_set]]
    for index in range(current_index + 1, len(waves)):
        waves[index] = [task_id for task_id in waves[index] if task_id not in scope_set]
    # Only remove empty *later* waves, preserving the current wave index.
    return [*waves[: current_index + 1], *[wave for wave in waves[current_index + 1 :] if wave]]


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
        # A focused input interrupt may intentionally expose only the
        # branching slot (for example, an ambiguous recipient), while its
        # metadata retains the complete transaction batch.  Resume every
        # non-terminal sibling so a prepared self-transfer/airtime leg is not
        # stranded in ``AWAITING_CONFIRMATION`` and omitted from the combined
        # review.
        batch_scope_ids: list[str] = []
        if interrupt.kind == "input":
            batch_scope_ids = _active_batch_scope_task_ids(state=state, interrupt=interrupt)
        task_ids_to_reset = _ordered_reset_task_ids(
            state=state,
            interrupt_task_ids=[*original_task_ids, *batch_scope_ids],
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
                    task.payload["skip_extraction"] = True
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
                task.payload["skip_extraction"] = True
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

        # Preserve the typed destination kind across selection/slot resumes.
        # A sibling's callback or an extractor re-run must not turn a linked
        # account leg into an external-recipient task.
        for task_id in task_ids_to_reset:
            task = state_view.task(task_id)
            if task is not None and task.type == "transfer" and task.payload.get("is_self") is True:
                clear_external_recipient_bindings_for_self(task.payload)
        last_interrupt = interrupt
        if interrupt.kind == "confirmation" and task_ids_to_reset != [str(task_id) for task_id in interrupt.task_ids]:
            if hasattr(interrupt, "model_copy"):
                last_interrupt = interrupt.model_copy(update={"task_ids": task_ids_to_reset})
        updates = {
            "pending_interrupt": None,
            "last_interrupt": last_interrupt,
            "tasks": state_view.tasks,
            "pin_verified": False,
            "authorization_context": None,
            "last_callback": None,
        }
        if interrupt.kind == "input":
            if merged_waves := _merge_batch_scope_into_current_wave(
                state=state,
                batch_task_ids=batch_scope_ids,
            ):
                updates["waves"] = merged_waves
                logger.info(
                    "batch_input_scope_reunited_for_resume",
                    task_count=len(batch_scope_ids),
                    current_wave_index=wave_position(state).index,
                )
        return updates
    return {}


__all__ = ["_continue_flow_updates"]
