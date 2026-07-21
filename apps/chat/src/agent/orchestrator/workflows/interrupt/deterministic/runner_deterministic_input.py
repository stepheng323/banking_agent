"""Deterministic input interrupt shortcuts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.recipient_review import recipient_review_signature
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.batch_input_scope import (
    resolve_batch_input_message_scope,
)
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
from banking.transactions.shared.account_selection.reference import match_source_account_reference
from banking.transactions.shared.confirmation.classifier import classify_confirmation_reply_sync
from banking.transactions.shared.confirmation.models import ConfirmationPromptKind
from shared.money import to_naira


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


def _single_funding_source_choice_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if getattr(interrupt, "kind", None) != "input":
        return None
    task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) or []]
    if len(task_ids) != 1:
        return None
    task_id = task_ids[0]
    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    if set(fields_by_task.get(task_id) or []) != {"source_accounts", "explicit_split"}:
        return None

    metadata = getattr(interrupt, "metadata", {}) or {}
    if metadata.get("intent") != "single_funding_source_choice":
        return None
    candidate_ids = [str(value) for value in metadata.get("candidate_source_ids", []) if str(value).strip()]
    anchor_ids = [str(value) for value in metadata.get("anchor_source_ids", []) if str(value).strip()]
    if not candidate_ids or len(anchor_ids) != 1:
        return None

    state_view = interrupt_state_view(state)
    task = state_view.task(task_id)
    if task is None or task.type != "transfer":
        return None
    loaded = state_view.loaded_context_or_empty
    raw_accounts = loaded.get("transaction_accounts") or loaded.get("accounts") or loaded.get("all_accounts")
    accounts = [account for account in raw_accounts or [] if isinstance(account, dict)]
    by_id = {
        str(account.get("id") or account.get("account_id")): account
        for account in accounts
        if account.get("id") or account.get("account_id")
    }
    anchor = by_id.get(anchor_ids[0])
    candidates = [by_id[candidate_id] for candidate_id in candidate_ids if candidate_id in by_id]
    if anchor is None or not candidates:
        return None

    stripped = runtime.text.strip()
    selected: dict[str, Any] | None = None
    if stripped.isdigit():
        index = int(stripped)
        if 1 <= index <= len(candidates):
            selected = candidates[index - 1]
    else:
        selected = match_source_account_reference(runtime.text, candidates)
    if selected is None:
        return None

    anchor_bank = str(anchor.get("bank_name") or anchor.get("bank") or "").strip()
    selected_bank = str(selected.get("bank_name") or selected.get("bank") or "").strip()
    primary_contribution = to_naira(metadata.get("primary_contribution"))
    remaining_amount = to_naira(metadata.get("remaining_amount"))
    if (
        not anchor_bank
        or not selected_bank
        or primary_contribution is None
        or remaining_amount is None
        or primary_contribution <= 0
        or remaining_amount <= 0
    ):
        return None

    payload_override = {
        "source_accounts": [anchor_bank, selected_bank],
        "use_dual_accounts": True,
        "explicit_split": {
            anchor_bank: float(primary_contribution),
            selected_bank: float(remaining_amount),
        },
        "source_account_id": None,
        "source_account_name": None,
        "source_account_number": None,
        "source_bank_name": None,
        "source_account_index": None,
        "source_affinity_mode": "explicit",
        "funding_plan": None,
        "suggested_funding_plan": None,
        "confirmation": {"confirmed": False},
        "skip_extraction": True,
    }
    logger.info(
        "single_funding_source_choice_applied",
        candidate_count=len(candidates),
        selection_mode="index" if stripped.isdigit() else "account_reference",
    )
    return _continue_flow_updates(
        state,
        interrupt,
        precomputed_payload_overrides={task_id: payload_override},
    )


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

    single_funding_updates = _single_funding_source_choice_updates(state=state, runtime=runtime)
    if single_funding_updates is not None:
        return single_funding_updates

    batch_scope = resolve_batch_input_message_scope(state=state, interrupt=interrupt, text=runtime.text)
    if batch_scope:
        logger.info("guided_batch_input_shortcut_hit", target_task_count=len(batch_scope))
        return _continue_flow_updates(state, interrupt, input_messages_by_task=batch_scope)

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
