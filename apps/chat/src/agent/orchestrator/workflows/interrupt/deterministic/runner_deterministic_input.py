"""Deterministic input interrupt shortcuts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.recipient_review import recipient_review_signature
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import (
    _continue_flow_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_reprompt import (
    _input_greeting_reprompt_text,
    _reprompt_or_reset_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_selection_route import (
    _resolve_deterministic_input_selection_route,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_slot_route import (
    _resolve_deterministic_input_slot_route,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    TRANSACTION_INTENTS,
    _input_interrupt_required_fields,
    _is_input_interrupt_greeting,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.transactions.shared.confirmation.classifier import classify_confirmation_reply_sync
from banking.transactions.shared.confirmation.models import ConfirmationPromptKind


def _has_transfer_destination(payload: dict[str, Any]) -> bool:
    recipient_account = payload.get("recipient_account") or payload.get("recipient_account_number")
    recipient_bank = payload.get("recipient_bank_name") or payload.get("recipient_bank_code")
    return bool(str(recipient_account or "").strip() and str(recipient_bank or "").strip())


def _approval_context(*, interrupt: PendingInterrupt, prompt_kind: ConfirmationPromptKind) -> str:
    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    return (
        f"Active prompt kind: {prompt_kind}\n"
        f"Prompt: {getattr(interrupt, 'prompt', None) or 'None'}\n"
        f"Task ids: {', '.join(str(task_id) for task_id in getattr(interrupt, 'task_ids', []) or [])}\n"
        f"Fields by task: {fields_by_task}"
    )


async def _is_prompt_approval(
    *,
    runtime: InterruptRuntime,
    prompt_kind: ConfirmationPromptKind,
) -> bool:
    state_view = runtime.state_view
    fastpath = classify_confirmation_reply_sync(
        runtime.text,
        prompt_kind=prompt_kind,
        locale=state_view.shortcut_locale,
    )
    if fastpath.is_approval:
        return True
    if not fastpath.is_unclear:
        return False

    task_planner = runtime.task_planner
    if task_planner is None or not hasattr(task_planner, "classify_confirmation_reply"):
        return False

    try:
        decision = await task_planner.classify_confirmation_reply(
            runtime.text,
            prompt_kind=prompt_kind,
            locale=state_view.current_locale,
            context=_approval_context(interrupt=runtime.interrupt, prompt_kind=prompt_kind),
            path_label="interrupt_path",
        )
    except Exception as exc:
        logger.warning(
            "input_prompt_approval_semantic_failed",
            prompt_kind=prompt_kind,
            error_type=type(exc).__name__,
        )
        return False
    return bool(getattr(decision, "is_approval", False))


async def _recipient_review_acceptance_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if getattr(interrupt, "kind", None) != "input":
        return None

    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) or []]
    if not task_ids:
        return None
    if not all("recipient_review_confirmed" in (fields_by_task.get(task_id) or []) for task_id in task_ids):
        return None

    if not await _is_prompt_approval(runtime=runtime, prompt_kind="recipient_review"):
        return None

    state_view = interrupt_state_view(state)
    payload_overrides: dict[str, dict[str, Any]] = {}
    resume_fields_by_task: dict[str, list[str]] = {}
    resume_prompts: list[str] = []
    resume_outbox_entries: list[dict[str, Any]] = []
    for task_id in task_ids:
        task = state_view.task(task_id)
        payload = task.payload if task is not None and isinstance(task.payload, dict) else {}
        signature = recipient_review_signature(payload)
        if not signature:
            return None
        resume_fields = payload.get("recipient_review_resume_fields")
        if isinstance(resume_fields, list) and resume_fields:
            resume_fields_by_task[task_id] = [str(field) for field in resume_fields if str(field).strip()]
        resume_prompt = payload.get("recipient_review_resume_prompt")
        if isinstance(resume_prompt, str) and resume_prompt.strip():
            resume_prompts.append(resume_prompt)
        resume_outbox = payload.get("recipient_review_resume_outbox")
        if isinstance(resume_outbox, dict):
            resume_outbox_entries.append(dict(resume_outbox))
        payload_overrides[task_id] = {
            "recipient_review_confirmed": True,
            "recipient_review_required": False,
            "recipient_review_signature": signature,
            "recipient_review_resume_fields": None,
            "recipient_review_resume_prompt": None,
            "recipient_review_resume_outbox": None,
        }

    logger.info("recipient_review_accepted", task_ids=task_ids)
    updates = _continue_flow_updates(state, interrupt, precomputed_payload_overrides=payload_overrides)
    if resume_fields_by_task:
        prompt = resume_prompts[0] if resume_prompts else interrupt.prompt
        resume_interrupt = PendingInterrupt(
            kind="input",
            task_ids=list(resume_fields_by_task),
            fields_by_task=resume_fields_by_task,
            prompt=prompt,
        )
        updates["pending_interrupt"] = resume_interrupt
        updates["last_interrupt"] = None
        if prompt:
            updates["outbox"] = resume_outbox_entries or [{"type": "say", "text": prompt}]
    else:
        updates["last_interrupt"] = None
    return updates


async def _suggested_funding_acceptance_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if getattr(interrupt, "kind", None) != "input":
        return None

    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) or []]
    if not task_ids:
        return None
    if not all("suggested_funding_plan" in (fields_by_task.get(task_id) or []) for task_id in task_ids):
        return None

    if not await _is_prompt_approval(runtime=runtime, prompt_kind="funding_suggestion"):
        return None

    state_view = interrupt_state_view(state)
    payload_overrides: dict[str, dict[str, Any]] = {}
    missing_destination_task_ids: list[str] = []
    for task_id in task_ids:
        task = state_view.task(task_id)
        payload = task.payload if task is not None and isinstance(task.payload, dict) else {}
        suggested_plan = payload.get("suggested_funding_plan")
        if not isinstance(suggested_plan, dict) or not suggested_plan:
            return None
        if task is not None and task.type == "transfer" and not _has_transfer_destination(payload):
            missing_destination_task_ids.append(task_id)
        payload_overrides[task_id] = {
            "funding_plan": suggested_plan,
            "suggested_funding_plan": None,
        }

    if missing_destination_task_ids:
        logger.warning(
            "suggested_funding_acceptance_missing_destination_rejected",
            task_ids=missing_destination_task_ids,
        )
        reset_overrides = {
            task_id: {
                "funding_plan": None,
                "suggested_funding_plan": None,
            }
            for task_id in task_ids
        }
        return _continue_flow_updates(state, interrupt, precomputed_payload_overrides=reset_overrides)

    logger.info("suggested_funding_plan_accepted", task_ids=task_ids)
    return _continue_flow_updates(state, interrupt, precomputed_payload_overrides=payload_overrides)


async def _input_shortcut_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    recipient_review_updates = await _recipient_review_acceptance_updates(state=state, runtime=runtime)
    if recipient_review_updates is not None:
        return recipient_review_updates

    suggested_funding_updates = await _suggested_funding_acceptance_updates(state=state, runtime=runtime)
    if suggested_funding_updates is not None:
        return suggested_funding_updates

    for route in (
        _resolve_deterministic_input_selection_route(state=state, interrupt=interrupt, text=runtime.text),
        _resolve_deterministic_input_slot_route(state=state, interrupt=interrupt, text=runtime.text),
    ):
        if route is None:
            continue
        logger.info(
            "interrupt_input_shortcut_hit",
            kind=interrupt.kind,
            decision=route.decision,
            reason=route.reason,
        )
        return _continue_flow_updates(state, interrupt)
    return None


async def _input_greeting_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if not (
        interrupt.kind == "input"
        and _is_input_interrupt_greeting(runtime.text)
        and runtime.current_task_types.intersection(TRANSACTION_INTENTS)
    ):
        return None

    logger.info(
        "interrupt_input_greeting_nudge",
        task_ids=interrupt.task_ids,
        required_fields=sorted(_input_interrupt_required_fields(interrupt)),
    )
    return await _reprompt_or_reset_updates(
        state,
        interrupt,
        runtime.redis_client,
        prompt_override=_input_greeting_reprompt_text(state, interrupt, runtime.current_task_types),
    )


__all__ = [
    "_input_greeting_updates",
    "_input_shortcut_updates",
    "_recipient_review_acceptance_updates",
    "_suggested_funding_acceptance_updates",
]
