"""Deterministic answers for questions about the active interrupt flow."""

from __future__ import annotations

import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.return_to_flow import append_return_to_flow_tail
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_requirements import (
    _build_requirements_hint,
    _friendly_required_field,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.status.status_query_text import (
    _build_status_query_response,
)
from banking.presentation.formatters.currency import format_naira
from shared.types.planner import ActiveFlowQuestionType, InterruptRouteDecision
from shared.utils.network_utils import format_network_display_name

QUESTION_OVERRIDE_CONFIDENCE_THRESHOLD = 0.55

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "amount": ("amount", "how much", "value", "money"),
    "recipient": ("recipient", "beneficiary", "person", "who", "name"),
    "recipient_account": ("account number", "acct", "account"),
    "recipient_bank_name": ("bank",),
    "source_account_id": ("source", "from", "debit", "pay from", "use account"),
    "account_id": ("linked account", "account selection", "which account", "choose account"),
    "phone": ("phone", "line", "number"),
    "network": ("network", "mtn", "airtel", "glo", "9mobile"),
    "data_plan_id": ("plan", "bundle", "data"),
    "data_plan_preference": ("budget", "size", "gb", "validity", "monthly", "weekly"),
    "beneficiary_id": ("choose", "select", "beneficiary", "which one", "why choose"),
    "date_range": ("date", "range", "period", "when"),
    "transaction_reference": ("transaction", "reference", "receipt", "payment"),
    "schedule_id": ("schedule", "scheduled", "standing order"),
    "schedule_time": ("time", "what time", "when is", "when will"),
    "schedule_status": ("status", "pending", "active"),
    "pin": ("pin", "password", "authorize", "auth"),
    "confirmation_summary": ("confirm", "confirmation", "yes", "no"),
}

_REQUIREMENT_REASONS: dict[str, str] = {
    "recipient_name": "I need the recipient name so I know who this request is for.",
    "recipient_account": "I need the account number so I can resolve the exact recipient account.",
    "recipient_bank_name": "I need the bank so I can verify the account number against the right bank.",
    "recipient_phone": "I need the phone number so I know which line to recharge.",
    "target_phone": "I need the phone number so I know which line to use.",
    "phone": "I need the phone number so I know which line to use.",
    "network": "I need the mobile network so I can use the right biller and avoid sending it to the wrong provider.",
    "data_plan_id": "I need the data plan choice so I know the exact bundle to buy.",
    "data_plan_preference": "I need a budget, size, or validity preference so I can narrow the available data plans.",
    "beneficiary_id": "I need you to choose a beneficiary because more than one saved recipient could match.",
    "source_account_id": "I need the source account so I know which linked account to debit.",
    "amount": "I need the amount before I can continue.",
    "pin": "Your PIN authorizes this request. I will not continue without it.",
    "confirmation_summary": "I need your confirmation before I continue.",
    "date_range": "I need the date range so I can search the right transactions.",
    "time_range": "I need the date range so I can search the right transactions.",
    "transaction_reference": "I need the transaction or reference so support can check the right payment.",
    "reference": "I need the reference so support can check the right record.",
    "account_id": "I need the account selection so I know which linked account you mean.",
    "account_selection": "I need the account selection so I know which linked account you mean.",
    "identifier": "I need the identifier or selection so I know which record you mean.",
    "authorization": "I need authorization so I can safely continue this account request.",
    "schedule_id": "I need the scheduled transaction selection so I know which schedule you mean.",
    "schedule_selector": "I need the scheduled transaction selection so I know which schedule you mean.",
    "schedule_time": "I need the schedule time so I know exactly when this should run.",
}

_SEPARATE_BANKING_TASK_PATTERNS = (
    r"\b(balance|account balance|how much is in my account|available balance)\b",
    r"\b(show|list|view|check)\b.*\b(transaction|transactions|history|spend|spent|expenses?)\b",
    r"\b(spend|spent|expenses?|transaction history|transactions?)\b",
    r"\b(scheduled transactions?|schedule list|show schedule|show scheduled)\b",
    r"\b(fail(?:ed|ure)?|debited|refund|complain|complaint|support|ticket)\b",
)

_FRESH_TASK_PATTERNS = (
    r"\b(send|transfer)\b.+\b(to|for)\b",
    r"\b(buy|purchase|top up|recharge)\b.+\b(airtime|data)\b",
)


def classify_deterministic_active_flow_question(
    *,
    text: str,
    interrupt: PendingInterrupt,
    current_task_types: set[str],
) -> InterruptRouteDecision | None:
    """Classify obvious active-flow questions when the router is absent or unsure."""

    normalized = _normalize(text)
    if not normalized or not _looks_like_question(normalized):
        return None
    if _matches_any(normalized, _SEPARATE_BANKING_TASK_PATTERNS):
        return None
    if _matches_any(normalized, _FRESH_TASK_PATTERNS):
        return None

    question_type: ActiveFlowQuestionType
    target_field = _infer_target_field(normalized, interrupt)
    unsafe_reason: str | None = None

    if _contains_any(normalized, ("reverse", "reversal", "refund", "undo", "recall")):
        question_type = "unsupported_or_unsafe"
        unsafe_reason = "future_reversal"
    elif _contains_any(normalized, ("should i", "should we", "advise", "advice")):
        question_type = "unsupported_or_unsafe"
        unsafe_reason = "financial_advice"
    elif _contains_any(normalized, ("legit", "trust", "scam", "safe to send", "real person")):
        question_type = "unsupported_or_unsafe"
        unsafe_reason = "recipient_trust"
    elif _contains_any(
        normalized,
        (
            "guarantee",
            "instant",
            "arrive",
            "how long",
            "when will",
            "when would",
            "what time",
            "which time",
            "status",
            "pending",
            "when is",
        ),
    ):
        question_type = "timing_or_status"
    elif _contains_any(normalized, ("fee", "fees", "charge", "charges", "cost")):
        question_type = "fees_or_charges"
    elif _contains_any(normalized, ("why pin", "why do you need pin", "why authorization", "is pin safe")):
        question_type = "auth_pin_reason"
        target_field = target_field or "pin"
    elif _contains_any(normalized, ("why", "why do you need", "why is", "why are you asking")):
        question_type = "why_required"
    elif _contains_any(normalized, ("what do you need", "what is needed", "which details", "requirements")):
        question_type = "requirements"
    elif _contains_any(normalized, ("where are we", "what are we doing", "recap", "where did we stop")):
        question_type = "recap"
    elif _contains_any(normalized, ("cancel", "if i cancel", "what if i say no", "say no")):
        question_type = "cancellation_effect"
    elif _contains_any(normalized, ("what happens if i confirm", "what if i say yes", "if i confirm", "if i approve")):
        question_type = "confirmation_effect"
    elif _contains_any(normalized, ("source account", "which account", "from where", "pay from", "debit")):
        question_type = "source_account"
        target_field = target_field or "source_account_id"
    elif _contains_any(normalized, ("can i change", "can i edit", "can i update", "what can i change")):
        question_type = "editable_fields"
    elif _contains_any(normalized, ("who", "how much", "which", "what plan", "what network", "what phone")):
        question_type = "current_value"
    else:
        question_type = "unknown"

    return InterruptRouteDecision(
        decision="active_flow_question",
        confidence=0.78,
        detected_language=None,
        target_intent=None,
        target_mode=None,
        status_query_type=None,
        question_type=question_type,
        target_field=target_field,
        unsafe_reason=unsafe_reason,
        reason="deterministic_active_flow_question",
    )


def active_flow_question_updates(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    route: InterruptRouteDecision,
    current_task_types: set[str],
    semantic_path_shape: str = "active_flow_question",
) -> dict[str, Any]:
    """Return a non-destructive answer for an active-flow question."""

    state_view = interrupt_state_view(state)
    response = _build_active_flow_question_response(
        state=state,
        interrupt=interrupt,
        route=route,
        current_task_types=current_task_types,
    )
    logger.info(
        "interrupt_active_flow_question_hit",
        kind=interrupt.kind,
        question_type=route.question_type or "unknown",
        target_field=route.target_field,
        unsafe_reason=route.unsafe_reason,
        active_types=sorted(current_task_types),
    )
    return {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "tasks": state_view.tasks,
        "outbox": [{"type": "say", "text": response}],
        "semantic_path_shape": semantic_path_shape,
    }


def should_apply_deterministic_active_flow_question(
    *,
    route: InterruptRouteDecision,
    question_route: InterruptRouteDecision | None,
) -> bool:
    """Decide whether a deterministic question read should replace router output."""

    if question_route is None:
        return False
    if route.decision in {"active_flow_question", "status_query"}:
        return False
    question_type = question_route.question_type or "unknown"
    if route.decision == "switch_intent" and route.confidence >= QUESTION_OVERRIDE_CONFIDENCE_THRESHOLD:
        return False
    if route.decision == "continue_flow" and question_type == "unknown":
        return route.confidence < QUESTION_OVERRIDE_CONFIDENCE_THRESHOLD
    if route.decision in {"cancel", "approve_flow", "reject_flow"}:
        return question_type != "unknown"
    if route.decision == "unclear":
        return True
    if route.confidence < QUESTION_OVERRIDE_CONFIDENCE_THRESHOLD:
        return True
    return question_type != "unknown"


def _build_active_flow_question_response(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    route: InterruptRouteDecision,
    current_task_types: set[str],
) -> str:
    question_type = route.question_type or "unknown"
    if question_type in {"recap", "requirements"}:
        return _build_status_query_response(
            state=state,
            interrupt=interrupt,
            task_types=current_task_types,
            status_query_type=question_type,
        )
    if question_type == "why_required":
        response = _why_required_response(state=state, interrupt=interrupt, route=route)
    elif question_type == "confirmation_effect":
        response = _confirmation_effect_response(interrupt)
    elif question_type == "cancellation_effect":
        response = _cancellation_effect_response(current_task_types)
    elif question_type == "auth_pin_reason":
        response = _auth_pin_reason_response(current_task_types)
    elif question_type == "source_account":
        response = _source_account_response(state=state, interrupt=interrupt)
    elif question_type == "editable_fields":
        response = _editable_fields_response(current_task_types)
    elif question_type == "current_value":
        response = _current_value_response(state=state, interrupt=interrupt, route=route)
    elif question_type == "timing_or_status":
        response = _timing_or_status_response(state=state, interrupt=interrupt)
    elif question_type == "fees_or_charges":
        response = _fees_or_charges_response(state=state, interrupt=interrupt)
    elif question_type == "unsupported_or_unsafe":
        return _unsupported_or_unsafe_response(route)
    else:
        return _unknown_response(state=state, interrupt=interrupt, current_task_types=current_task_types)

    if response.startswith("I cannot answer that safely"):
        return response
    return append_return_to_flow_tail(
        message=response,
        state=state,
        interrupt=interrupt,
        target_field=route.target_field,
    )


def _why_required_response(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    route: InterruptRouteDecision,
) -> str:
    required_fields = _required_fields(interrupt)
    target_field = route.target_field
    if target_field not in required_fields:
        target_field = _best_required_field(target_field, required_fields)

    if target_field and target_field in _REQUIREMENT_REASONS:
        return _REQUIREMENT_REASONS[target_field]

    if required_fields:
        needed = ", ".join(_friendly_required_field(field) for field in required_fields)
        hint = _build_requirements_hint(required_fields, interrupt.kind)
        prefix = f"I need {needed} to continue safely."
        return f"{prefix} {hint}".strip()

    return _unknown_response(state=state, interrupt=interrupt, current_task_types=set())


def _confirmation_effect_response(interrupt: PendingInterrupt) -> str:
    if interrupt.kind == "confirmation":
        return (
            "If you confirm, I will continue with the request shown for confirmation. "
            "No money is sent until any required authorization is completed."
        )
    if interrupt.kind == "auth":
        return "Authorization lets me continue with the request you already confirmed."
    return "After you provide the missing details, I will show you a confirmation before anything is sent."


def _cancellation_effect_response(current_task_types: set[str]) -> str:
    if current_task_types and current_task_types.issubset(TRANSACTION_INTENTS):
        return "If you cancel, this pending transaction will be cancelled. No money will be sent."
    return "If you cancel, this pending request will be cancelled."


def _auth_pin_reason_response(current_task_types: set[str]) -> str:
    if current_task_types and current_task_types.issubset(TRANSACTION_INTENTS):
        return "Your PIN authorizes this transaction. I will not send money or buy bills without it."
    return "Your PIN authorizes this request. I will not continue without it."


def _source_account_response(*, state: OrchestratorState, interrupt: PendingInterrupt) -> str:
    payload = _first_payload(state, interrupt)
    source = _source_account_label(payload)
    if source:
        return f"This will use {source}. You can tell me another account or bank if you want to change it."
    return "You can tell me which linked account or bank to use before continuing."


def _editable_fields_response(current_task_types: set[str]) -> str:
    if "transfer" in current_task_types:
        return (
            "You can change the amount, recipient, bank/account details, narration, "
            "or source account before authorization."
        )
    if "airtime" in current_task_types:
        return "You can change the amount, phone line, network, or source account before authorization."
    if "data" in current_task_types:
        return "You can change the phone line, network, data plan, budget/size, or source account before authorization."
    if "query" in current_task_types:
        return "You can change the date range, filters, or transaction detail you want me to search for."
    if "support" in current_task_types:
        return "You can change the transaction reference, issue details, or support category before I continue."
    if "beneficiary" in current_task_types:
        return "You can choose a different beneficiary option or give more details to narrow the match."
    if "schedule" in current_task_types:
        return "You can change the schedule selection, timing, amount, or recipient before I continue."
    return "You can change the required details before continuing. Tell me what to update."


def _current_value_response(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    route: InterruptRouteDecision,
) -> str:
    task = _first_task(state, interrupt)
    task_type = task.type if task is not None else ""
    payload = task.payload if task is not None and isinstance(task.payload, dict) else {}
    field = route.target_field or ""
    if not field:
        field = _best_required_field(None, _required_fields(interrupt)) or ""

    value = _field_value(payload, field, task_type)
    if value:
        return value

    if field:
        friendly = _friendly_required_field(field)
        return f"I do not have the {friendly} yet."

    summary = _payload_current_summary(payload, task_type)
    if summary:
        return summary

    return _unknown_response(state=state, interrupt=interrupt, current_task_types={task_type} if task_type else set())


def _timing_or_status_response(*, state: OrchestratorState, interrupt: PendingInterrupt) -> str:
    task = _first_task(state, interrupt)
    payload = task.payload if task is not None and isinstance(task.payload, dict) else {}
    if task is not None and task.type == "schedule":
        schedule_time = _schedule_time_label(payload)
        status = _string(payload.get("status") or payload.get("schedule_status") or payload.get("state"))
        if schedule_time and status:
            return f"This schedule is {status} and set for {schedule_time}."
        if schedule_time:
            return f"This schedule is set for {schedule_time}."
        if status:
            return f"This schedule is currently {status}."

    if interrupt.kind == "input":
        return "This has not been submitted yet. I am still waiting for the missing details."
    if interrupt.kind == "confirmation":
        return "This has not been submitted yet. I am waiting for your confirmation."
    if interrupt.kind == "auth":
        return "This has not been submitted yet. I am waiting for authorization."
    return "This request is still pending."


def _fees_or_charges_response(*, state: OrchestratorState, interrupt: PendingInterrupt) -> str:
    payload = _first_payload(state, interrupt)
    fee = payload.get("fee") or payload.get("fees") or payload.get("charge") or payload.get("charges")
    fee_text = _format_amount(fee)
    if fee_text:
        return f"The fee shown for this request is {fee_text}."
    return "I do not have a fee to show for this step. If a fee applies, it should be shown before authorization."


def _unsupported_or_unsafe_response(route: InterruptRouteDecision) -> str:
    reason = (route.unsafe_reason or "").strip().lower()
    if reason == "financial_advice":
        return (
            "I cannot decide whether you should send money. If you want to continue, "
            "confirm the transaction; otherwise say cancel."
        )
    if reason == "provider_guarantee":
        return (
            "I cannot guarantee bank or provider timing. I can continue once you confirm, "
            "and I will show the status after processing."
        )
    if reason == "recipient_trust":
        return (
            "I cannot verify whether the recipient is trustworthy. "
            "Please confirm the recipient details yourself before continuing."
        )
    if reason == "future_reversal":
        return (
            "If you are unsure, cancel now. Once a successful transfer is sent, "
            "it cannot be reversed or refunded from here."
        )
    return "I cannot answer that safely for this pending request."


def _unknown_response(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    current_task_types: set[str],
) -> str:
    if interrupt.kind == "confirmation":
        return "I cannot answer that safely here. Please confirm to continue or say cancel."
    if interrupt.kind == "auth":
        return "I cannot answer that safely here. Complete PIN authorization to continue, or say cancel."
    requirements = _build_status_query_response(
        state=state,
        interrupt=interrupt,
        task_types=current_task_types,
        status_query_type="requirements",
    )
    return f"I cannot answer that safely here. {requirements}".strip()


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


def _first_task(state: OrchestratorState, interrupt: PendingInterrupt) -> TaskSpec | None:
    state_view = interrupt_state_view(state)
    for task_id in interrupt.task_ids:
        task = state_view.task(str(task_id))
        if task is not None:
            return task
    return None


def _first_payload(state: OrchestratorState, interrupt: PendingInterrupt) -> dict[str, Any]:
    task = _first_task(state, interrupt)
    return task.payload if task is not None and isinstance(task.payload, dict) else {}


def _source_account_label(payload: dict[str, Any]) -> str | None:
    bank = _string(payload.get("source_bank_name"))
    last4 = _last4(payload.get("source_account_number"))
    if bank and last4:
        return f"{bank} (...{last4})"
    if bank:
        return bank
    return None


def _field_value(payload: dict[str, Any], field: str, task_type: str) -> str | None:
    if field in {"amount"}:
        amount = _format_amount(payload.get("amount"))
        return f"The amount is {amount}." if amount else None
    if field in {"recipient", "recipient_name"}:
        recipient = _string(payload.get("recipient_resolved_name") or payload.get("recipient_name"))
        if not recipient and task_type in {"airtime", "data"}:
            phone = _phone_label(payload)
            return f"This is for {phone}." if phone else None
        return f"This is going to {recipient}." if recipient else None
    if field == "recipient_account":
        account = _string(payload.get("recipient_account"))
        return f"The recipient account number is {account}." if account else None
    if field == "recipient_bank_name":
        bank = _string(payload.get("recipient_bank_name"))
        return f"The recipient bank is {bank}." if bank else None
    if field in {"source_account", "source_account_id"}:
        source = _source_account_label(payload)
        return f"The source account is {source}." if source else None
    if field in {"phone", "recipient_phone", "target_phone"}:
        phone = _phone_label(payload)
        return f"The phone line is {phone}." if phone else None
    if field == "network":
        network = format_network_display_name(_string(payload.get("network")))
        return f"The network is {network}." if network else None
    if field in {"data_plan_id", "data_plan_preference", "plan"}:
        plan = _string(payload.get("plan_name") or payload.get("biller_item_name") or payload.get("plan"))
        amount = _format_amount(payload.get("amount"))
        if plan and amount:
            return f"The selected plan is {plan} for {amount}."
        if plan:
            return f"The selected plan is {plan}."
        if amount:
            return f"The budget is {amount}."
    if field in {"account_id", "account_selection"}:
        account = _account_label(payload)
        return f"The selected account is {account}." if account else None
    if field in {"beneficiary_id", "beneficiary_selection"}:
        options = _option_labels(
            payload.get("matches")
            or payload.get("candidates")
            or payload.get("beneficiary_candidates")
            or payload.get("referent_recipient_candidates")
        )
        if options:
            return f"The matching options are: {', '.join(options)}."
    if field in {"schedule_id", "schedule_selector"}:
        schedule = _schedule_label(payload)
        return f"The selected schedule is {schedule}." if schedule else None
    if field in {"schedule_time", "time"}:
        schedule_time = _schedule_time_label(payload)
        return f"The schedule time is {schedule_time}." if schedule_time else None
    if field in {"schedule_status", "status"}:
        status = _string(payload.get("status") or payload.get("schedule_status") or payload.get("state"))
        return f"The schedule status is {status}." if status else None
    if field == "confirmation_summary":
        summary = _string(payload.get("confirmation_summary") or payload.get("summary"))
        return f"The confirmation is: {summary}" if summary else None
    return None


def _payload_current_summary(payload: dict[str, Any], task_type: str) -> str | None:
    if task_type in {"airtime", "data"}:
        parts = [
            _field_value(payload, "amount", task_type),
            _field_value(payload, "phone", task_type),
            _field_value(payload, "network", task_type),
            _field_value(payload, "source_account_id", task_type),
        ]
        if task_type == "data":
            parts.append(_field_value(payload, "data_plan_id", task_type))
        return _join_sentences(parts)
    if task_type == "account":
        account = _account_label(payload)
        action = _string(payload.get("action") or payload.get("account_action"))
        if account and action:
            return f"This account request is for {account}: {action}."
        if account:
            return f"This account request is for {account}."
    if task_type == "beneficiary":
        options = _option_labels(
            payload.get("matches")
            or payload.get("candidates")
            or payload.get("beneficiary_candidates")
            or payload.get("referent_recipient_candidates")
        )
        if options:
            return f"The matching beneficiary options are: {', '.join(options)}."
    if task_type == "schedule":
        schedule = _schedule_label(payload)
        return f"The selected schedule is {schedule}." if schedule else None
    return None


def _format_amount(value: Any) -> str | None:
    try:
        return format_naira(float(value))
    except (TypeError, ValueError):
        return None


def _infer_target_field(normalized: str, interrupt: PendingInterrupt) -> str | None:
    required_fields = _required_fields(interrupt)
    if _contains_any(normalized, ("account number", "acct")):
        return _best_required_field("recipient_account", required_fields) or "recipient_account"
    if _contains_any(normalized, ("source account", "which account", "from where", "pay from", "debit")):
        return _best_required_field("source_account_id", required_fields) or "source_account_id"
    for field, aliases in _FIELD_ALIASES.items():
        if _contains_any(normalized, aliases):
            return _best_required_field(field, required_fields) or field
    return _best_required_field(None, required_fields)


def _looks_like_question(normalized: str) -> bool:
    return _contains_any(
        normalized,
        (
            "why",
            "what",
            "where",
            "who",
            "which",
            "how",
            "when",
            "can i",
            "could i",
            "will it",
            "would",
            "should i",
            "is this",
            "is it",
            "are we",
            "do you",
            "do i",
        ),
    )


def _matches_any(normalized: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, normalized) for pattern in patterns)


def _contains_any(normalized: str, needles: tuple[str, ...]) -> bool:
    return any(needle in normalized for needle in needles)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _phone_label(payload: dict[str, Any]) -> str | None:
    return _string(payload.get("target_phone") or payload.get("phone") or payload.get("recipient_phone"))


def _account_label(payload: dict[str, Any]) -> str | None:
    bank = _string(
        payload.get("bank_name")
        or payload.get("account_bank_name")
        or payload.get("source_bank_name")
        or payload.get("selected_bank_name")
    )
    last4 = _last4(
        payload.get("account_number") or payload.get("source_account_number") or payload.get("selected_account_number")
    )
    if bank and last4:
        return f"{bank} (...{last4})"
    if bank:
        return bank
    account_id = _string(payload.get("account_id") or payload.get("source_account_id"))
    return account_id


def _schedule_label(payload: dict[str, Any]) -> str | None:
    title = _string(payload.get("schedule_label") or payload.get("description") or payload.get("recipient_name"))
    schedule_time = _schedule_time_label(payload)
    if title and schedule_time:
        return f"{title} at {schedule_time}"
    return title or schedule_time or _string(payload.get("schedule_id") or payload.get("schedule_selector"))


def _schedule_time_label(payload: dict[str, Any]) -> str | None:
    start_date = _string(payload.get("schedule_start_date") or payload.get("date") or payload.get("start_date"))
    local_time = _string(payload.get("schedule_time_local") or payload.get("time") or payload.get("time_local"))
    recurrence = _string(payload.get("recurrence_type") or payload.get("schedule_mode"))
    parts = [part for part in (start_date, local_time, recurrence) if part]
    return " ".join(parts) if parts else None


def _option_labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    labels: list[str] = []
    for index, item in enumerate(value[:3], start=1):
        if isinstance(item, str):
            label = item.strip()
        elif isinstance(item, dict):
            label = _option_dict_label(item)
        else:
            label = _string(item) or ""
        if label:
            labels.append(f"{index}. {label}")
    return labels


def _option_dict_label(item: dict[str, Any]) -> str:
    name = _string(item.get("alias") or item.get("name") or item.get("recipient_name") or item.get("display_name"))
    bank = _string(item.get("bank_name") or item.get("network"))
    account_last4 = _last4(item.get("account_number") or item.get("recipient_account") or item.get("phone"))
    parts = [part for part in (name, bank, f"...{account_last4}" if account_last4 else None) if part]
    return " ".join(parts)


def _join_sentences(parts: list[str | None]) -> str | None:
    sentences = [part.strip() for part in parts if part and part.strip()]
    if not sentences:
        return None
    return " ".join(sentences)


def _last4(value: Any) -> str | None:
    text = _string(value)
    if not text:
        return None
    digits = "".join(char for char in text if char.isdigit())
    return digits[-4:] if len(digits) >= 4 else None


__all__ = [
    "QUESTION_OVERRIDE_CONFIDENCE_THRESHOLD",
    "active_flow_question_updates",
    "classify_deterministic_active_flow_question",
    "should_apply_deterministic_active_flow_question",
]
