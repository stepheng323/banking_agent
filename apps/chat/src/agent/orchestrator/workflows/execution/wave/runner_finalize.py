import time
from enum import StrEnum
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.auth_gate_updates import _build_auth_gate_updates
from apps.chat.src.agent.orchestrator.workflows.execution.blocker_arbitration import choose_wave_blocker
from apps.chat.src.agent.orchestrator.workflows.execution.common import _with_policy_notice
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_gate_updates import (
    _build_confirmation_gate_updates,
)
from apps.chat.src.agent.orchestrator.workflows.execution.control_state import execution_control_state
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompts import (
    _build_missing_field_interrupt_updates,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import (
    all_existing_tasks_terminal,
    get_task,
    task_log_shapes,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import cancel_task
from apps.chat.src.agent.orchestrator.workflows.execution.turn_metadata import turn_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_setup import ExecutionWaveRuntime
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import (
    _fail_stalled_wave_tasks,
    next_wave_index,
)
from apps.chat.src.agent.orchestrator.workflows.runtime_config import OrchestrationConfig
from banking.presentation.i18n.renderer import render_message
from shared.observability.llm import ainvoke_with_config, build_llm_runnable_config
from shared.observability.llm_call_metrics import record_llm_call
from shared.utils.logging import get_logger
from shared.utils.user_error import safe_user_error_message

logger = get_logger(__name__)


class ResponseCompositionPlan(StrEnum):
    PASS_THROUGH = "pass_through"
    DETERMINISTIC_STACK = "deterministic_stack"
    PRESERVE_SEPARATE = "preserve_separate"
    LLM_BRIDGE = "llm_bridge"
    LEGACY_BRIDGE = "legacy_bridge"


class OutboxBridgeDecision(BaseModel):
    """Short, fact-free transitions inserted between immutable response fragments."""

    transitions: list[str] = Field(default_factory=list, max_length=2)


def _composition_hint(entry: dict[str, Any]) -> dict[str, str]:
    raw = entry.get("_composition")
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if isinstance(value, (str, int, float))}


def _composition_plan(entries: list[dict[str, Any]]) -> ResponseCompositionPlan:
    if len(entries) <= 1:
        return ResponseCompositionPlan.PASS_THROUGH
    if any("body_blocks" in entry or "actionable_payload" in entry for entry in entries):
        return ResponseCompositionPlan.PRESERVE_SEPARATE

    hints = [_composition_hint(entry) for entry in entries]
    if not all(hints):
        # Preserve the historical quality for unannotated output until worker
        # result builders publish their composition hints.
        return ResponseCompositionPlan.LEGACY_BRIDGE if len(entries) <= 3 else ResponseCompositionPlan.PRESERVE_SEPARATE

    modes = {hint.get("merge_mode", "") for hint in hints}
    priorities = {hint.get("priority", "") for hint in hints}
    shapes = {hint.get("response_shape", "") for hint in hints}
    if (
        modes & {"preserve", "separate"}
        or priorities & {"safety", "required_action"}
        or shapes & {"confirmation", "actionable", "surface_list", "surface_detail"}
    ):
        return ResponseCompositionPlan.PRESERVE_SEPARATE
    if modes == {"stackable"} or len(entries) > 3:
        return ResponseCompositionPlan.DETERMINISTIC_STACK
    return ResponseCompositionPlan.LLM_BRIDGE


def _deterministic_stack(texts: list[str]) -> str:
    return "\n\n".join(text for text in texts if text.strip())


def _remove_suppressed_actionable_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Never present an actionable card beside an unresolved input surface.

    A native option card is itself a pending input contract.  Treat its
    presence as authoritative at the final outbox boundary as well as the
    accumulator blocker check; this protects against a custom worker or stale
    checkpoint that emitted a confirmation candidate without registering the
    corresponding input request.
    """
    return [
        entry
        for entry in entries
        if entry.get("type") not in {"request_confirmation", "request_pin"}
    ]


def _safe_transition(value: str) -> str | None:
    normalized = " ".join(value.split()).strip()
    if not normalized or len(normalized) > 120 or any(char.isdigit() for char in normalized):
        return None
    return normalized


async def _bridge_texts(
    texts: list[str],
    entries: list[dict[str, Any]],
    runtime: ExecutionWaveRuntime,
    *,
    plan: ResponseCompositionPlan,
) -> str:
    """Use the conversation model only to connect immutable independent results."""
    runtime_config = OrchestrationConfig.from_runnable_config(runtime.ctx.config)
    responder = runtime_config.configurable.get("conversation_responder")
    llm = getattr(responder, "llm", None)
    if not llm:
        return _deterministic_stack(texts)

    structured_llm = llm.with_structured_output(OutboxBridgeDecision)

    system_template = (
        "Write only one very short transition for each gap between independent banking response fragments. "
        "Do not restate, alter, summarize, or add facts, amounts, names, actions, advice, greetings, or questions. "
        "The application inserts immutable fragments itself. Use the requested locale."
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_template),
            ("user", "Fragment families in order: {families}\nLocale: {locale}"),
        ]
    )

    config = build_llm_runnable_config(
        role="conversation",
        phone_number=turn_metadata(runtime.ctx.state).phone_number,
        path_label="outbox_bridge",
        task_domain="orchestrator",
        locale=runtime.locale,
    )

    chain = prompt | structured_llm
    families = [(_composition_hint(entry).get("response_family") or "banking_result") for entry in entries]

    start = time.perf_counter()
    system_chars = len(system_template)
    output_chars = 0
    try:
        result = await ainvoke_with_config(
            chain,
            {"families": ", ".join(families), "locale": runtime.locale},
            config=config,
            role="conversation",
            deadline_seconds=2.5,
        )
        transitions = [_safe_transition(value) for value in result.transitions]
        if len(transitions) != len(texts) - 1 or any(value is None for value in transitions):
            raise ValueError("invalid_outbox_bridge")
        safe_transitions = [value for value in transitions if value is not None]
        parts = [texts[0]]
        for transition, text in zip(safe_transitions, texts[1:], strict=True):
            parts.extend((transition, text))
        blended = "\n\n".join(parts)
        output_chars = len(" ".join(safe_transitions))
    except Exception as e:
        logger.info("outbox_bridge_fallback", plan=plan.value, error_type=type(e).__name__)
        blended = _deterministic_stack(texts)

    duration_ms = (time.perf_counter() - start) * 1000
    model = getattr(llm, "model_name", None) or getattr(llm, "model", None) or "unknown"

    logger.info(
        "outbox_bridge_llm_call",
        duration_ms=round(duration_ms, 2),
        input_messages=len(texts),
        plan=plan.value,
    )
    record_llm_call(
        event_name="outbox_bridge_llm_call",
        duration_ms=duration_ms,
        model=model,
        response_type="OutboxBridgeDecision",
        system_chars=system_chars,
        user_chars=len(", ".join(families)),
        latency_span="outbox_bridge_llm",
        output_json_chars=output_chars,
        extra_fields={"composition_plan": plan.value, "fragment_count": len(texts)},
    )
    return blended


async def _compose_say_group(entries: list[dict[str, Any]], runtime: ExecutionWaveRuntime) -> list[dict[str, Any]]:
    plan = _composition_plan(entries)
    texts = [str(entry.get("text") or "") for entry in entries if str(entry.get("text") or "").strip()]
    logger.info("outbox_composition_selected", plan=plan.value, fragment_count=len(entries))
    if plan is ResponseCompositionPlan.PRESERVE_SEPARATE:
        return entries
    if not texts:
        return entries[:1]
    if plan is ResponseCompositionPlan.PASS_THROUGH:
        combined = dict(entries[0])
        combined["text"] = texts[0]
        return [combined]
    combined = dict(entries[0])
    if plan in {ResponseCompositionPlan.LLM_BRIDGE, ResponseCompositionPlan.LEGACY_BRIDGE}:
        combined["text"] = await _bridge_texts(texts, entries, runtime, plan=plan)
    else:
        combined["text"] = _deterministic_stack(texts)
    return [combined]


def _strip_private_outbox_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if not key.startswith("_")}


def _current_wave_is_terminal(
    *,
    state: OrchestratorState,
    current_wave: list[str],
) -> bool:
    return all_existing_tasks_terminal(state, current_wave)


def _advance_or_fail_stalled_wave(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    accumulator: ExecutionAccumulator,
) -> None:
    if _current_wave_is_terminal(state=state, current_wave=current_wave):
        if not accumulator.has_current_wave_index():
            accumulator.set_current_wave_index(next_wave_index(state))
        return

    if accumulator.has_pending_interrupt():
        return

    stalled = _fail_stalled_wave_tasks(
        state=state,
        current_wave=current_wave,
        reason="task made no terminal or blocking progress",
    )
    logger.error(
        "advance_wave_stalled_without_stop_condition",
        wave=current_wave,
        stalled_tasks=stalled,
        task_shapes=task_log_shapes(state, stalled),
    )
    accumulator.set_current_wave_index(next_wave_index(state))


def _preconfirmation_batch_failure(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    accumulator: ExecutionAccumulator,
    locale: str,
) -> dict[str, Any] | None:
    """Handle a pre-confirmation batch when one leg fails.

    A failed leg must not disappear behind a sibling's input/confirmation
    prompt.  If another leg is still live, preserve that leg and surface the
    failure alongside its next gate; the user can review/continue the ready
    item or repair the failed one.  Only an all-failed pre-confirmation batch
    is aborted outright.
    """
    transaction_types = {"transfer", "airtime", "data"}
    transaction_tasks = [
        task
        for task_id in current_wave
        if (task := get_task(state, task_id)) is not None and task.type in transaction_types
    ]
    failed_tasks = [task for task in transaction_tasks if task.stage == TaskStage.FAILED]
    if not failed_tasks or len(transaction_tasks) < 2:
        return None

    if any(task.stage == TaskStage.COMPLETED for task in transaction_tasks):
        return None
    if not (
        accumulator.input_request_count()
        or accumulator.confirmation_task_ids()
        or accumulator.auth_task_ids()
    ):
        return None

    error_items = [
        (task.type, str(task.payload.get("error") or ""))
        for task in failed_tasks
        if str(task.payload.get("error") or "").strip()
    ]
    if not error_items:
        error_items = [(failed_tasks[0].type, render_message("orchestrator.finalize.failed_unknown", locale))]

    error_lines = [
        render_message(
            "orchestrator.finalize.failed_prefix",
            locale,
            {
                "error": safe_user_error_message(error, task_type=task_type, locale=locale)
            },
        )
        for task_type, error in error_items
    ]
    message = "\n\n".join(dict.fromkeys(error_lines))

    live_tasks = [
        task
        for task in transaction_tasks
        if task.stage not in {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}
    ]
    if live_tasks:
        partial_notice = render_message(
            "orchestrator.finalize.batch_partial_failure",
            locale,
            {"failure": message},
        )
        for task in failed_tasks:
            # The lifecycle reducer must not emit the same failure a second
            # time.  Keep the notice on the failed leg so the next input,
            # confirmation, or auth gate can present it with the live leg.
            task.payload["_batch_failure_reported"] = True
            task.payload["_batch_failure_notice"] = partial_notice
        logger.warning(
            "batch_partial_failure_preserved",
            failed_task_count=len(failed_tasks),
            live_task_count=len(live_tasks),
            unresolved_input_count=accumulator.input_request_count(),
        )
        return None

    # No live leg remains. Stop the already-blocked batch rather than leaving
    # an orphaned input/confirmation interrupt behind.
    for task in transaction_tasks:
        if task.stage not in {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}:
            cancel_task(
                task,
                render_message(
                    "orchestrator.finalize.batch_aborted",
                    locale,
                ),
            )
        task.payload["_batch_failure_reported"] = True

    message = (
        f"{message}\n\n"
        f"{render_message('orchestrator.finalize.batch_aborted', locale)}"
    )
    accumulator.set_outbox([{"type": "say", "text": message}])
    accumulator.clear_pending_interrupt()
    accumulator.set_current_wave_index(next_wave_index(state))
    logger.warning(
        "batch_aborted_before_confirmation",
        failed_task_count=len(failed_tasks),
        transaction_task_count=len(transaction_tasks),
        unresolved_input_count=accumulator.input_request_count(),
    )
    return accumulator.to_updates()


def _attach_partial_batch_failure_notice(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Keep a preserved leg's failure visible at every remaining gate."""
    notices: list[str] = []
    for task_id in current_wave:
        task = get_task(state, task_id)
        notice = task.payload.get("_batch_failure_notice") if task else None
        if isinstance(notice, str) and notice.strip() and notice not in notices:
            notices.append(notice)
    if not notices:
        return updates

    notice = "\n\n".join(notices)
    outbox = updates.get("outbox")
    if isinstance(outbox, list):
        # A pending selection is already a complete interactive card.  Put
        # the failed-leg status in that card's title instead of sending a
        # separate ``say`` immediately before the buttons.  Besides being
        # easier to scan, this preserves the invariant that the option ids
        # belong only to the focused task; the failed sibling has no options
        # and cannot accidentally consume the selection reply.
        option_entry = next(
            (entry for entry in outbox if isinstance(entry, dict) and entry.get("type") == "show_options"),
            None,
        )
        if option_entry is not None:
            title = str(option_entry.get("title") or "").strip()
            option_entry["title"] = f"{notice}\n\n{title}" if title else notice
            option_entry["_batch_status"] = "partial_failure"
            logger.info(
                "batch_failure_folded_into_selection_card",
                option_count=len(option_entry.get("options") or []),
                active_task_count=len(getattr(updates.get("pending_interrupt"), "task_ids", []) or []),
            )
        else:
            updates["outbox"] = [{"type": "say", "text": notice}, *outbox]

    pending = updates.get("pending_interrupt")
    prompt = getattr(pending, "prompt", None)
    if pending is not None and isinstance(prompt, str) and prompt.strip() and hasattr(pending, "model_copy"):
        updates["pending_interrupt"] = pending.model_copy(update={"prompt": f"{notice}\n\n{prompt}"})
    return updates


async def _coalesce_outbox(outbox: list[dict[str, Any]], runtime: ExecutionWaveRuntime) -> list[dict[str, Any]]:
    if not outbox:
        return []

    # Native option surfaces are pending input, even when a custom worker
    # forgot to register its input blocker.  Never let a stale confirmation
    # candidate leak beside the buttons during composition.
    if any(entry.get("type") == "show_options" for entry in outbox):
        outbox = _remove_suppressed_actionable_entries(outbox)

    coalesced: list[dict[str, Any]] = []
    say_group: list[dict[str, Any]] = []

    async def flush_say_group() -> None:
        nonlocal say_group
        if say_group:
            coalesced.extend(await _compose_say_group(say_group, runtime))
            say_group = []

    for entry in outbox:
        if entry.get("type") == "say":
            say_group.append(dict(entry))
        else:
            await flush_say_group()
            coalesced.append(entry)

    await flush_say_group()

    return [_strip_private_outbox_metadata(entry) for entry in coalesced]


async def finalize_execution_wave_updates(
    *,
    state: OrchestratorState,
    runtime: ExecutionWaveRuntime,
) -> dict[str, Any]:
    if failed_batch_updates := _preconfirmation_batch_failure(
        state=state,
        current_wave=runtime.current_wave,
        accumulator=runtime.accumulator,
        locale=runtime.locale,
    ):
        return failed_batch_updates

    blocker = choose_wave_blocker(state=state, current_wave=runtime.current_wave, agg=runtime.accumulator)
    # Input is a batch-wide gate.  A sibling may already be confirmation-ready,
    # but no review surface is valid while another task still has unresolved
    # fields.  Keep this invariant here as a final safety net for accumulator
    # updates produced by custom workers.
    if blocker.kind in {"confirmation", "auth"} and runtime.accumulator.input_request_count() > 0:
        logger.warning(
            "execution_gate_input_overrides_actionable_blocker",
            requested_blocker=blocker.kind,
            unresolved_task_count=runtime.accumulator.input_request_count(),
        )
        blocker = choose_wave_blocker(state=state, current_wave=runtime.current_wave, agg=runtime.accumulator)
    if blocker.kind == "input":
        updates = _build_missing_field_interrupt_updates(
            state=state,
            current_wave=runtime.current_wave,
            agg=runtime.accumulator,
            locale=runtime.locale,
        )
        updates = _attach_partial_batch_failure_notice(
            state=state,
            current_wave=runtime.current_wave,
            updates=updates,
        )
        if "outbox" in updates and isinstance(updates["outbox"], list):
            updates["outbox"] = _remove_suppressed_actionable_entries(updates["outbox"])
            updates["outbox"] = await _coalesce_outbox(updates["outbox"], runtime)
        return updates

    if execution_control_state(state).has_policy_notice:
        runtime.accumulator.set_outbox(_with_policy_notice(state, runtime.accumulator.get_outbox()))
        runtime.accumulator.clear_policy_notice()

    if blocker.kind == "confirmation":
        updates = _build_confirmation_gate_updates(
            state=state,
            current_wave=runtime.current_wave,
            agg=runtime.accumulator,
            locale=runtime.locale,
            task_ids=blocker.task_ids,
        )
        updates = _attach_partial_batch_failure_notice(
            state=state,
            current_wave=runtime.current_wave,
            updates=updates,
        )
        if "outbox" in updates and isinstance(updates["outbox"], list):
            updates["outbox"] = await _coalesce_outbox(updates["outbox"], runtime)
        return updates

    if blocker.kind == "auth":
        updates = _build_auth_gate_updates(
            state=state,
            current_wave=runtime.current_wave,
            agg=runtime.accumulator,
            locale=runtime.locale,
            task_ids=blocker.task_ids,
        )
        updates = _attach_partial_batch_failure_notice(
            state=state,
            current_wave=runtime.current_wave,
            updates=updates,
        )
        if "outbox" in updates and isinstance(updates["outbox"], list):
            updates["outbox"] = await _coalesce_outbox(updates["outbox"], runtime)
        return updates

    _advance_or_fail_stalled_wave(
        state=state,
        current_wave=runtime.current_wave,
        accumulator=runtime.accumulator,
    )
    updates = runtime.accumulator.to_updates()
    if "outbox" in updates and isinstance(updates["outbox"], list):
        updates["outbox"] = await _coalesce_outbox(updates["outbox"], runtime)
    return updates


__all__ = ["finalize_execution_wave_updates"]
