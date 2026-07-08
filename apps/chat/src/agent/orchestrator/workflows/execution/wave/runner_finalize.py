import time
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
from shared.observability.llm import build_llm_runnable_config
from shared.observability.llm_call_metrics import record_llm_call
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OutboxSynthesisDecision(BaseModel):
    blended_text: str = Field(
        description="The seamlessly blended, natural conversational paragraph combining all inputs."
    )


async def _synthesize_texts(texts: list[str], runtime: ExecutionWaveRuntime) -> str:
    runtime_config = OrchestrationConfig.from_runnable_config(runtime.ctx.config)
    task_planner = runtime_config.configurable.get("task_planner")
    llm = getattr(task_planner, "planner_llm", None)
    if not llm:
        return "\n\n".join(texts)

    structured_llm = llm.with_structured_output(OutboxSynthesisDecision)

    system_template = (
        "You are a helpful, professional banking assistant. Combine the following distinct "
        "updates or questions into a single, cohesive, natural conversational response. "
        "Ensure smooth transitions between topics. Do not add any new information, pleasantries, "
        "or greetings. Keep the exact tone of the original messages.\n\n"
        "IMPORTANT: The final blended message must be written in the following locale/language: {locale}."
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_template),
            ("user", "Messages to blend:\n{messages}"),
        ]
    )

    config = build_llm_runnable_config(
        role="planner",
        phone_number=runtime.ctx.state.phone_number,
        path_label="outbox_synthesis",
        task_domain="orchestrator",
        locale=runtime.locale,
    )

    chain = prompt | structured_llm
    messages_str = "\n---\n".join(f"Message {i + 1}:\n{t}" for i, t in enumerate(texts))

    start = time.perf_counter()
    system_chars = len(system_template)
    user_chars = len(messages_str)
    output_chars = 0
    try:
        result = await chain.ainvoke({"messages": messages_str, "locale": runtime.locale}, config=config)
        blended = result.blended_text
        output_chars = len(blended)
    except Exception as e:
        logger.error("outbox_synthesis_failed", error=str(e))
        blended = "\n\n".join(texts)

    duration_ms = (time.perf_counter() - start) * 1000
    model = getattr(llm, "model_name", None) or getattr(llm, "model", None) or "unknown"

    logger.info(
        "outbox_synthesis_llm_call",
        duration_ms=round(duration_ms, 2),
        input_messages=len(texts),
    )
    record_llm_call(
        event_name="outbox_synthesis_llm_call",
        duration_ms=duration_ms,
        model=model,
        response_type="OutboxSynthesisDecision",
        system_chars=system_chars,
        user_chars=user_chars,
        latency_span="outbox_synthesis_llm",
        output_json_chars=output_chars,
    )
    return blended


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
    current_say: dict[str, Any] | None = None
    texts_to_blend: list[str] = []

    for entry in outbox:
        if entry.get("type") == "say":
            if current_say is not None and ("body_blocks" in entry or "body_blocks" in current_say):
                if texts_to_blend:
                    if len(texts_to_blend) > 1:
                        blended_text = await _synthesize_texts(texts_to_blend, runtime)
                        current_say["text"] = blended_text
                    else:
                        current_say["text"] = texts_to_blend[0]
                coalesced.append(current_say)
                current_say = None
                texts_to_blend = []

            if current_say is None:
                current_say = dict(entry)
                if "body_blocks" in current_say:
                    current_say["body_blocks"] = list(current_say["body_blocks"])
                if entry.get("text"):
                    texts_to_blend.append(entry["text"])
            else:
                if entry.get("text"):
                    texts_to_blend.append(entry["text"])

                if "actionable_payload" in entry and "actionable_payload" not in current_say:
                    current_say["actionable_payload"] = entry["actionable_payload"]
        else:
            if current_say is not None:
                if texts_to_blend:
                    if len(texts_to_blend) > 1:
                        blended_text = await _synthesize_texts(texts_to_blend, runtime)
                        current_say["text"] = blended_text
                    else:
                        current_say["text"] = texts_to_blend[0]
                coalesced.append(current_say)
                current_say = None
                texts_to_blend = []
            coalesced.append(entry)

    if current_say is not None:
        if texts_to_blend:
            if len(texts_to_blend) > 1:
                blended_text = await _synthesize_texts(texts_to_blend, runtime)
                current_say["text"] = blended_text
            else:
                current_say["text"] = texts_to_blend[0]
        coalesced.append(current_say)

    return coalesced


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
