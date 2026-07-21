import time
from enum import StrEnum
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

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
    task_log_shapes,
)
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_setup import ExecutionWaveRuntime
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import (
    _fail_stalled_wave_tasks,
    next_wave_index,
)
from apps.chat.src.agent.orchestrator.workflows.runtime_config import OrchestrationConfig
from shared.observability.llm import ainvoke_with_config, build_llm_runnable_config
from shared.observability.llm_call_metrics import record_llm_call
from shared.utils.logging import get_logger

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
        phone_number=runtime.ctx.state.phone_number,
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


async def _coalesce_outbox(outbox: list[dict[str, Any]], runtime: ExecutionWaveRuntime) -> list[dict[str, Any]]:
    if not outbox:
        return []

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
    blocker = choose_wave_blocker(state=state, current_wave=runtime.current_wave, agg=runtime.accumulator)
    if blocker.kind == "input":
        updates = _build_missing_field_interrupt_updates(
            state=state,
            current_wave=runtime.current_wave,
            agg=runtime.accumulator,
            locale=runtime.locale,
        )
        if "outbox" in updates and isinstance(updates["outbox"], list):
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
