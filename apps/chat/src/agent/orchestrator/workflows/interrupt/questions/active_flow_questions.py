"""Deterministic answers for questions about the active interrupt flow."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    unsupported_capability_params,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import (
    get_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
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
from banking.presentation.i18n.renderer import render_message
from shared.money import to_naira
from shared.types.planner import InterruptRouteDecision
from shared.utils.bank_aliases import get_bank_search_terms, normalize_bank_name
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
    r"^\s*(?:my\s+)?balance\s*$",
    r"\b(check|what is my|what's my|whats my|what my|show|tell me my)\s+(?:.*?\s+)?balance\b",
    r"\b(account balance|how much is in my account|available balance)\b",
    r"\b(show|list|view|check)\b.*\b(transaction|transactions|history|spend|spent|expenses?)\b",
    r"\b(spend|spent|expenses?|transaction history|transactions?)\b",
    r"\b(scheduled transactions?|schedule list|show schedule|show scheduled)\b",
    r"\b(fail(?:ed|ure)?|debited|refund|complain|complaint|support|ticket)\b",
)

_FRESH_TASK_PATTERNS = (
    r"\b(send|transfer)\b.+\b(to|for)\b",
    r"\b(buy|purchase|top up|recharge)\b.+\b(airtime|data)\b",
)


async def active_flow_question_updates(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    route: InterruptRouteDecision,
    current_task_types: set[str],
    path_shape: str = "active_flow_question",
    services: Any | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Return a non-destructive answer for an active-flow question."""

    state_view = interrupt_state_view(state)
    response = await _build_active_flow_question_response(
        state=state,
        interrupt=interrupt,
        route=route,
        current_task_types=current_task_types,
        services=services,
        message=message,
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
        "path_shape": path_shape,
    }


async def _build_active_flow_question_response(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    route: InterruptRouteDecision,
    current_task_types: set[str],
    services: Any | None,
    message: str,
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
    elif question_type == "funding_affordability":
        response = await _funding_affordability_response(
            state=state,
            interrupt=interrupt,
            services=services,
            message=message,
        )
    elif question_type == "unsupported_or_unsafe":
        response = _unsupported_or_unsafe_response(route, state)
        if response.startswith("I cannot") or response.startswith("If you are unsure"):
            return response
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

    # Check if this is a contextual balance/percentage calculation query
    pct = payload.get("transfer_percentage")
    amount = payload.get("amount")
    transfer_all = payload.get("transfer_all")
    if amount is not None and (pct is not None or transfer_all):
        amount_val = float(amount)
        source = _source_account_label(payload) or payload.get("source_bank_name") or "your account"
        if pct is not None and float(pct) > 0:
            pct_val = float(pct)
            balance = round((amount_val * 100.0) / pct_val, 2)
            balance_text = format_naira(balance)
            amount_text = format_naira(amount_val)
            pct_str = f"{int(pct_val)}%" if pct_val.is_integer() else f"{pct_val}%"
            return f"Yes, {amount_text} is {pct_str} of your {source} balance (which is {balance_text})."
        elif transfer_all:
            amount_text = format_naira(amount_val)
            return f"Yes, {amount_text} is your entire {source} balance."

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


async def _funding_affordability_response(
    *,
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    services: Any | None,
    message: str,
) -> str:
    """Answer a pending-batch affordability question without changing the batch."""

    locale = interrupt_state_view(state).current_locale
    total, incomplete, task_count = _pending_batch_total(state, interrupt)
    if incomplete or total is None:
        logger.info(
            "interrupt_funding_affordability",
            outcome="incomplete_cost",
            task_count=task_count,
        )
        return render_message("funding.active_question.incomplete_cost", locale)

    account = _account_named_in_message(state, interrupt, message)
    if account is None:
        logger.info(
            "interrupt_funding_affordability",
            outcome="account_unresolved",
            task_count=task_count,
        )
        return render_message("funding.active_question.account_unresolved", locale)

    provider = getattr(getattr(services, "transfer", None), "dd_provider", None)
    account_id = _provider_account_id(account)
    if provider is None or not account_id:
        logger.info(
            "interrupt_funding_affordability",
            outcome="balance_unavailable",
            task_count=task_count,
        )
        return render_message("funding.active_question.balance_unavailable", locale)

    try:
        balance_result = await provider.get_balance(str(account_id), real_time=True)
        available = to_naira(getattr(balance_result, "available_balance", None)) if balance_result is not None else None
    except Exception:
        available = None
    if available is None:
        logger.info(
            "interrupt_funding_affordability",
            outcome="balance_unavailable",
            task_count=task_count,
        )
        return render_message("funding.active_question.balance_unavailable", locale)

    account_label = _linked_account_label(account)
    if available >= total:
        logger.info(
            "interrupt_funding_affordability",
            outcome="sufficient",
            task_count=task_count,
        )
        return render_message(
            "funding.active_question.sufficient",
            locale,
            {
                "account": account_label,
                "total": format_naira(total),
                "available": format_naira(available),
                "remaining": format_naira(available - total),
            },
        )
    logger.info(
        "interrupt_funding_affordability",
        outcome="insufficient",
        task_count=task_count,
    )
    return render_message(
        "funding.active_question.insufficient",
        locale,
        {
            "account": account_label,
            "total": format_naira(total),
            "available": format_naira(available),
            "shortfall": format_naira(total - available),
        },
    )


def _pending_batch_total(state: OrchestratorState, interrupt: PendingInterrupt) -> tuple[Decimal | None, bool, int]:
    state_view = interrupt_state_view(state)
    total = Decimal("0.00")
    task_count = 0
    for task_id in interrupt.task_ids:
        task = state_view.task(str(task_id))
        if task is None or task.type not in TRANSACTION_INTENTS or not isinstance(task.payload, dict):
            continue
        task_count += 1
        amount = to_naira(task.payload.get("amount"))
        if amount is None or amount <= 0:
            return None, True, task_count
        total += amount
        for fee_key in ("fee", "fees", "charge", "charges"):
            fee = to_naira(task.payload.get(fee_key))
            if fee is not None and fee > 0:
                total += fee
    return total, task_count == 0, task_count


def _account_named_in_message(
    state: OrchestratorState,
    interrupt: PendingInterrupt,
    message: str,
) -> dict[str, Any] | None:
    accounts = loaded_context(state).transaction_account_rows_or_account_rows
    normalized_message = _normal_account_text(message)
    matches = []
    for account in accounts:
        bank_name = str(account.get("bank_name") or account.get("institution_name") or "").strip()
        if not bank_name:
            continue
        terms = {_normal_account_text(term) for term in get_bank_search_terms(bank_name)}
        terms.add(_normal_account_text(normalize_bank_name(bank_name)))
        if any(term and term in normalized_message for term in terms):
            matches.append(account)
    if len(matches) == 1:
        return matches[0]

    # If the user did not name a bank, use an unambiguous source already
    # selected for every pending task.  We never infer a different source.
    source_ids = {
        str(task.payload.get("source_account_id") or "").strip()
        for task_id in interrupt.task_ids
        if (task := interrupt_state_view(state).task(str(task_id))) is not None and isinstance(task.payload, dict)
    }
    source_ids.discard("")
    if len(source_ids) == 1:
        return next(
            (
                account
                for account in accounts
                if str(account.get("id") or account.get("account_id") or "").strip() in source_ids
            ),
            None,
        )
    return None


def _normal_account_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _provider_account_id(account: dict[str, Any]) -> Any:
    return account.get("mono_account_id") or account.get("account_id") or account.get("id")


def _linked_account_label(account: dict[str, Any]) -> str:
    bank = _string(account.get("bank_name") or account.get("institution_name")) or "Your account"
    suffix = _last4(account.get("account_number"))
    return f"{bank} (...{suffix})" if suffix else bank


def _unsupported_or_unsafe_response(route: InterruptRouteDecision, state: OrchestratorState) -> str:
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

    # Try finding it in registry for other unsupported capabilities

    capability = get_unsupported_capability(reason)
    if capability is not None:
        state_view = interrupt_state_view(state)
        locale = state_view.current_locale
        params = unsupported_capability_params(capability, locale=locale)
        return render_message("capability.unsupported_unavailable", locale, params)

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
    if task_type in {"transfer", "send_money"}:
        amount = _format_amount(payload.get("amount"))
        recipient = _string(payload.get("recipient_resolved_name") or payload.get("recipient_name"))
        source = _source_account_label(payload)
        transfer_parts = []
        if amount:
            transfer_parts.append(f"The amount is {amount}.")
        if recipient:
            transfer_parts.append(f"This is going to {recipient}.")
        if source:
            transfer_parts.append(f"The source account is {source}.")
        return " ".join(transfer_parts) if transfer_parts else "This is a pending transfer."
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
    question_indicators = (
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
    )
    if _contains_any(normalized, question_indicators):
        return True

    if "?" in normalized:
        # If it has "?" but no other question indicator keywords,
        # ignore short phrases of 1-2 words (like "opay?") which are usually slot fills.
        clean_text = normalized.replace("?", "").strip()
        words = clean_text.split()
        if len(words) <= 2:
            return False
        return True

    return False


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
]
