"""Deterministic slot-entry shortcuts for input interrupts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_text import _digits_only
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_slot_amount_patterns import (
    _INPUT_AMOUNT_COMMAND_REPLY_RE,
    _INPUT_NUMERIC_AMOUNT_REPLY_RE,
    _INPUT_SIMPLE_AMOUNT_REPLY_RE,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_slot_data_plan_patterns import (
    _looks_like_data_plan_preference_reply,
    _looks_like_data_plan_selection_reply,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_slot_entity_patterns import (
    _INPUT_SELF_PHONE_REPLY_RE,
    _looks_like_bank_name_reply,
    _looks_like_network_reply,
    _looks_like_simple_transfer_recipient_reply,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.beneficiaries.services.selection import match_beneficiary_candidate_selection
from shared.types.planner import InterruptRouteDecision


def _continue_flow_decision(reason: str, *, confidence: float = 0.99) -> InterruptRouteDecision:
    return InterruptRouteDecision(
        decision="continue_flow",
        confidence=confidence,
        detected_language="English",
        target_intent=None,
        target_mode=None,
        status_query_type=None,
        reason=reason,
    )


def _resolve_deterministic_input_slot_route(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
) -> InterruptRouteDecision | None:
    state_view = interrupt_state_view(state)
    if getattr(interrupt, "kind", None) != "input":
        return None

    task_ids = getattr(interrupt, "task_ids", None) or []
    if len(task_ids) != 1:
        return None

    active_task = state_view.task(str(task_ids[0]))
    active_task_type = active_task.type if active_task is not None else None

    fields_by_task = getattr(interrupt, "fields_by_task", None) or {}
    required_fields = {field for field in (fields_by_task.get(str(task_ids[0])) or []) if isinstance(field, str)}
    if not required_fields:
        return None

    stripped_text = text.strip()
    numeric_text = _digits_only(text)

    if required_fields == {"beneficiary_id"}:
        beneficiary_candidates = []
        if active_task is not None and isinstance(active_task.payload, dict):
            raw_candidates = active_task.payload.get("beneficiary_candidates")
            if isinstance(raw_candidates, list):
                beneficiary_candidates = [c for c in raw_candidates if isinstance(c, dict)]
        if stripped_text.isdigit() or match_beneficiary_candidate_selection(stripped_text, beneficiary_candidates):
            return _continue_flow_decision("shortcut_input_beneficiary_selection")

    if required_fields == {"recipient_account"} and 8 <= len(numeric_text) <= 16:
        return _continue_flow_decision("shortcut_input_account_entry")

    phone_fields = {"recipient_phone", "phone", "target_phone"}
    if (
        required_fields in ({"recipient_phone"}, {"phone"}, {"target_phone"})
        or (active_task_type in {"airtime", "data"} and bool(required_fields & phone_fields))
    ) and 10 <= len(numeric_text) <= 15:
        return _continue_flow_decision("shortcut_input_phone_entry")

    if active_task_type in {"airtime", "data"} and (
        required_fields in ({"recipient_phone"}, {"phone"}, {"target_phone"}) or bool(required_fields & phone_fields)
    ):
        if _INPUT_SELF_PHONE_REPLY_RE.fullmatch(stripped_text):
            return _continue_flow_decision("shortcut_input_self_phone_entry")

    if (
        active_task_type in {"airtime", "data"}
        and "network" in required_fields
        and _looks_like_network_reply(stripped_text)
    ):
        return _continue_flow_decision("shortcut_input_network_entry")

    if active_task_type == "data" and required_fields == {"data_plan_id"}:
        if _looks_like_data_plan_selection_reply(stripped_text, active_task):
            return _continue_flow_decision("shortcut_input_data_plan_selection")

    if active_task_type == "data" and "data_plan_preference" in required_fields:
        if _looks_like_data_plan_preference_reply(stripped_text):
            return _continue_flow_decision("shortcut_input_data_plan_preference")

    if (
        required_fields == {"amount"}
        and (
            _INPUT_SIMPLE_AMOUNT_REPLY_RE.fullmatch(stripped_text)
            or _INPUT_AMOUNT_COMMAND_REPLY_RE.fullmatch(stripped_text)
        )
    ) or (
        active_task_type == "airtime"
        and "amount" in required_fields
        and _INPUT_NUMERIC_AMOUNT_REPLY_RE.fullmatch(stripped_text)
    ):
        return _continue_flow_decision("shortcut_input_amount_entry")

    if (
        active_task_type == "transfer"
        and required_fields == {"recipient_account", "recipient_bank_name"}
        and _looks_like_simple_transfer_recipient_reply(stripped_text)
    ):
        return _continue_flow_decision("shortcut_input_recipient_reply", confidence=0.95)

    if (
        active_task_type == "transfer"
        and required_fields == {"recipient_bank_name"}
        and _looks_like_bank_name_reply(stripped_text)
    ):
        return _continue_flow_decision("shortcut_input_bank_name_entry", confidence=0.95)

    return None


__all__ = [
    "_continue_flow_decision",
    "_resolve_deterministic_input_slot_route",
]
