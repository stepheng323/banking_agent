"""Return-to-flow copy for non-destructive interrupt replies."""

from __future__ import annotations

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_requirements import (
    _friendly_required_field,
)
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message

_INPUT_FIELD_TAIL_KEYS: dict[str, MessageKey] = {
    "amount": "interrupt.return_to_flow.input.amount",
    "recipient_account": "interrupt.return_to_flow.input.recipient_account",
    "recipient_bank_name": "interrupt.return_to_flow.input.recipient_bank_name",
    "recipient_phone": "interrupt.return_to_flow.input.phone",
    "target_phone": "interrupt.return_to_flow.input.phone",
    "phone": "interrupt.return_to_flow.input.phone",
    "network": "interrupt.return_to_flow.input.network",
    "data_plan_id": "interrupt.return_to_flow.input.data_plan_id",
    "data_plan_preference": "interrupt.return_to_flow.input.data_plan_preference",
    "beneficiary_id": "interrupt.return_to_flow.input.beneficiary_id",
    "source_account_id": "interrupt.return_to_flow.input.source_account_id",
    "account_id": "interrupt.return_to_flow.input.account_selection",
    "account_selection": "interrupt.return_to_flow.input.account_selection",
    "identifier": "interrupt.return_to_flow.input.account_selection",
    "transaction_reference": "interrupt.return_to_flow.input.transaction_reference",
    "reference": "interrupt.return_to_flow.input.transaction_reference",
    "date_range": "interrupt.return_to_flow.input.date_range",
    "time_range": "interrupt.return_to_flow.input.date_range",
    "schedule_id": "interrupt.return_to_flow.input.schedule_id",
    "schedule_selector": "interrupt.return_to_flow.input.schedule_id",
    "schedule_time": "interrupt.return_to_flow.input.schedule_time",
    "authorization": "interrupt.return_to_flow.input.authorization",
    "pin": "interrupt.return_to_flow.input.pin",
}

_INPUT_FIELD_GROUP_TAIL_KEYS: tuple[tuple[frozenset[str], MessageKey], ...] = (
    (
        frozenset({"recipient_account", "recipient_bank_name"}),
        "interrupt.return_to_flow.input.recipient_account_bank",
    ),
    (
        frozenset({"recipient_phone", "amount"}),
        "interrupt.return_to_flow.input.phone_amount",
    ),
    (
        frozenset({"target_phone", "amount"}),
        "interrupt.return_to_flow.input.phone_amount",
    ),
    (
        frozenset({"phone", "amount"}),
        "interrupt.return_to_flow.input.phone_amount",
    ),
    (
        frozenset({"recipient_phone", "network"}),
        "interrupt.return_to_flow.input.phone_network",
    ),
    (
        frozenset({"target_phone", "network"}),
        "interrupt.return_to_flow.input.phone_network",
    ),
    (
        frozenset({"phone", "network"}),
        "interrupt.return_to_flow.input.phone_network",
    ),
    (
        frozenset({"target_phone", "data_plan_preference"}),
        "interrupt.return_to_flow.input.phone_data_preference",
    ),
    (
        frozenset({"phone", "data_plan_preference"}),
        "interrupt.return_to_flow.input.phone_data_preference",
    ),
)


def _required_fields(interrupt: PendingInterrupt) -> list[str]:
    if not interrupt.task_ids:
        return []
    fields = (interrupt.fields_by_task or {}).get(interrupt.task_ids[0], [])
    return [field for field in fields if isinstance(field, str)]


def _best_required_field(target_field: str | None, required_fields: list[str]) -> str | None:
    if not required_fields:
        return target_field
    if target_field:
        if target_field in required_fields:
            return target_field
        if target_field == "date_range":
            for field in ("date_range", "time_range"):
                if field in required_fields:
                    return field
        if target_field == "phone":
            for field in ("recipient_phone", "target_phone", "phone"):
                if field in required_fields:
                    return field
        if target_field == "transaction_reference":
            for field in ("transaction_reference", "reference"):
                if field in required_fields:
                    return field
        for field in required_fields:
            if target_field in field or field in target_field:
                return field
    return required_fields[0]


def _group_tail_key(required_fields: list[str]) -> MessageKey | None:
    field_set = frozenset(required_fields)
    for group, message_key in _INPUT_FIELD_GROUP_TAIL_KEYS:
        if group.issubset(field_set):
            return message_key
    return None


def _task_skips_auth_after_confirmation(task: TaskSpec) -> bool:
    return (
        task.type == "schedule"
        and str(task.payload.get("action") or "").strip().lower() == "edit_scheduled_transaction"
        and task.payload.get("schedule_edit_requires_auth") is False
    )


def _confirmation_requires_auth(state: OrchestratorState, interrupt: PendingInterrupt) -> bool:
    state_view = interrupt_state_view(state)
    if state_view.pin_verified:
        return False

    saw_task = False
    for task_id in interrupt.task_ids:
        task = state_view.task(str(task_id))
        if task is None:
            continue
        saw_task = True
        if not _task_skips_auth_after_confirmation(task):
            return True
    return False if saw_task else False


def _input_return_to_flow_tail(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    target_field: str | None,
) -> str:
    locale = interrupt_state_view(state).current_locale
    required_fields = _required_fields(interrupt)

    if target_field:
        selected_field = _best_required_field(target_field, required_fields)
        if selected_field and (field_key := _INPUT_FIELD_TAIL_KEYS.get(selected_field)):
            return render_message(field_key, locale)

    if len(required_fields) > 1 and (group_key := _group_tail_key(required_fields)):
        return render_message(group_key, locale)

    selected_field = _best_required_field(target_field, required_fields)
    if selected_field and (field_key := _INPUT_FIELD_TAIL_KEYS.get(selected_field)):
        return render_message(field_key, locale)

    if len(required_fields) == 1 and selected_field:
        return render_message(
            "interrupt.return_to_flow.input.named_field",
            locale,
            {"field": _friendly_required_field(selected_field)},
        )
    return render_message("interrupt.return_to_flow.input.generic", locale)


def build_return_to_flow_tail(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    target_field: str | None = None,
) -> str:
    """Build the next valid user action for the current pending interrupt."""

    locale = interrupt_state_view(state).current_locale
    if interrupt.kind == "input":
        return _input_return_to_flow_tail(state=state, interrupt=interrupt, target_field=target_field)

    if interrupt.kind == "confirmation":
        message_key: MessageKey = (
            "interrupt.return_to_flow.confirmation.to_authorization"
            if _confirmation_requires_auth(state, interrupt)
            else "interrupt.return_to_flow.confirmation.to_continue"
        )
        return render_message(message_key, locale)

    if interrupt.kind == "auth":
        message_key = (
            "interrupt.return_to_flow.auth.pin"
            if (interrupt.auth_method or "").lower() == "pin"
            else "interrupt.return_to_flow.auth.generic"
        )
        return render_message(message_key, locale)

    return ""


def append_return_to_flow_tail(
    *,
    message: str,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    target_field: str | None = None,
) -> str:
    """Append return-to-flow copy unless the message already includes it."""

    text = message.strip()
    tail = build_return_to_flow_tail(state=state, interrupt=interrupt, target_field=target_field).strip()
    if not text or not tail or tail in text:
        return text

    separator = " " if text.endswith((".", "!", "?")) else ". "
    return f"{text}{separator}{tail}"


__all__ = [
    "append_return_to_flow_tail",
    "build_return_to_flow_tail",
]
