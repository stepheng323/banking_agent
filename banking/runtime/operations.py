"""Canonical operation registry for orchestrated banking workers.

The registry describes static execution capability. Runtime capability policy
remains authoritative for whether a registered operation is currently enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from banking.runtime.results import AccountResult, FAQResult, SupportResult, TransactionResult
from shared.types.planner import (
    AccountTaskParameters,
    AirtimeTaskParameters,
    BeneficiaryTaskParameters,
    DataTaskParameters,
    EmptyTaskParameters,
    QueryPreferenceTaskParameters,
    QueryTaskParameters,
    ScheduleTaskParameters,
    SupportTaskParameters,
    TransferTaskParameters,
)

OperationRisk = Literal["READ_ONLY", "MUTATION", "MONEY_MOVE"]
OperationDomain = Literal[
    "transfer",
    "account",
    "beneficiary",
    "airtime",
    "query",
    "data",
    "faq",
    "support",
    "schedule",
    "orchestrator",
]


@dataclass(frozen=True, slots=True)
class WorkerOperationSpec:
    """One canonical operation accepted by an orchestration executor."""

    domain: OperationDomain
    executor: OperationDomain
    action: str
    parameter_model: type[BaseModel]
    result_model: type[BaseModel]
    risk: OperationRisk
    requires_confirmation: bool
    requires_pin: bool
    policy_action: str


def _spec(
    domain: OperationDomain,
    executor: OperationDomain,
    action: str,
    parameter_model: type[BaseModel],
    result_model: type[BaseModel],
    risk: OperationRisk,
    *,
    confirmation: bool = False,
    pin: bool = False,
    policy_action: str | None = None,
) -> WorkerOperationSpec:
    return WorkerOperationSpec(
        domain=domain,
        executor=executor,
        action=action,
        parameter_model=parameter_model,
        result_model=result_model,
        risk=risk,
        requires_confirmation=confirmation,
        requires_pin=pin,
        policy_action=policy_action or action,
    )


_OPERATIONS = (
    _spec(
        "transfer",
        "transfer",
        "send_money",
        TransferTaskParameters,
        TransactionResult,
        "MONEY_MOVE",
        confirmation=True,
        pin=True,
    ),
    _spec(
        "schedule",
        "transfer",
        "schedule_transfer",
        TransferTaskParameters,
        TransactionResult,
        "MONEY_MOVE",
        confirmation=True,
        pin=True,
    ),
    _spec(
        "schedule",
        "transfer",
        "recurring_transfer",
        TransferTaskParameters,
        TransactionResult,
        "MONEY_MOVE",
        confirmation=True,
        pin=True,
    ),
    _spec(
        "airtime",
        "airtime",
        "buy_airtime",
        AirtimeTaskParameters,
        TransactionResult,
        "MONEY_MOVE",
        confirmation=True,
        pin=True,
    ),
    _spec(
        "schedule",
        "airtime",
        "schedule_airtime",
        AirtimeTaskParameters,
        TransactionResult,
        "MONEY_MOVE",
        confirmation=True,
        pin=True,
    ),
    _spec(
        "schedule",
        "airtime",
        "recurring_airtime",
        AirtimeTaskParameters,
        TransactionResult,
        "MONEY_MOVE",
        confirmation=True,
        pin=True,
    ),
    _spec("data", "data", "buy_data", DataTaskParameters, TransactionResult, "MONEY_MOVE", confirmation=True, pin=True),
    _spec("data", "data", "data_plan_query", DataTaskParameters, TransactionResult, "READ_ONLY"),
    _spec(
        "schedule",
        "data",
        "schedule_data",
        DataTaskParameters,
        TransactionResult,
        "MONEY_MOVE",
        confirmation=True,
        pin=True,
    ),
    _spec(
        "schedule",
        "data",
        "recurring_data",
        DataTaskParameters,
        TransactionResult,
        "MONEY_MOVE",
        confirmation=True,
        pin=True,
    ),
    _spec(
        "schedule", "schedule", "list_scheduled_transactions", ScheduleTaskParameters, TransactionResult, "READ_ONLY"
    ),
    _spec("schedule", "schedule", "find_scheduled_transaction", ScheduleTaskParameters, TransactionResult, "READ_ONLY"),
    _spec(
        "schedule",
        "schedule",
        "cancel_scheduled_transaction",
        ScheduleTaskParameters,
        TransactionResult,
        "MUTATION",
        confirmation=True,
    ),
    _spec(
        "schedule",
        "schedule",
        "edit_scheduled_transaction",
        ScheduleTaskParameters,
        TransactionResult,
        "MUTATION",
        confirmation=True,
        pin=True,
    ),
    _spec(
        "schedule",
        "schedule",
        "pause_scheduled_transaction",
        ScheduleTaskParameters,
        TransactionResult,
        "MUTATION",
        confirmation=True,
    ),
    _spec(
        "schedule",
        "schedule",
        "resume_scheduled_transaction",
        ScheduleTaskParameters,
        TransactionResult,
        "MUTATION",
        confirmation=True,
        pin=True,
    ),
    _spec("schedule", "schedule", "list_scheduled_runs", ScheduleTaskParameters, TransactionResult, "READ_ONLY"),
    _spec("schedule", "schedule", "find_scheduled_run", ScheduleTaskParameters, TransactionResult, "READ_ONLY"),
    _spec(
        "account",
        "account",
        "check_balance",
        AccountTaskParameters,
        AccountResult,
        "READ_ONLY",
        policy_action="list_accounts",
    ),
    _spec("account", "account", "list_accounts", AccountTaskParameters, AccountResult, "READ_ONLY"),
    _spec(
        "account", "account", "count", AccountTaskParameters, AccountResult, "READ_ONLY", policy_action="list_accounts"
    ),
    _spec(
        "account",
        "account",
        "get_default",
        AccountTaskParameters,
        AccountResult,
        "READ_ONLY",
        policy_action="list_accounts",
    ),
    _spec("account", "account", "link", AccountTaskParameters, AccountResult, "MUTATION", policy_action="link_account"),
    _spec(
        "account",
        "account",
        "unlink",
        AccountTaskParameters,
        AccountResult,
        "MUTATION",
        confirmation=True,
        policy_action="unlink_account",
    ),
    _spec("account", "account", "set_default", AccountTaskParameters, AccountResult, "MUTATION", confirmation=True),
    _spec(
        "account",
        "account",
        "reinitiate_mandate",
        AccountTaskParameters,
        AccountResult,
        "MUTATION",
        confirmation=True,
        policy_action="link_account",
    ),
    _spec(
        "beneficiary", "beneficiary", "list_beneficiaries", BeneficiaryTaskParameters, TransactionResult, "READ_ONLY"
    ),
    _spec(
        "beneficiary",
        "beneficiary",
        "delete_beneficiary",
        BeneficiaryTaskParameters,
        TransactionResult,
        "MUTATION",
        confirmation=True,
    ),
    _spec(
        "beneficiary",
        "beneficiary",
        "rename_beneficiary",
        BeneficiaryTaskParameters,
        TransactionResult,
        "MUTATION",
        confirmation=True,
    ),
    _spec(
        "beneficiary",
        "beneficiary",
        "save_beneficiary",
        BeneficiaryTaskParameters,
        TransactionResult,
        "MUTATION",
        confirmation=True,
        policy_action="save_verified_beneficiary",
    ),
    _spec(
        "query",
        "query",
        "transaction_search",
        QueryTaskParameters,
        TransactionResult,
        "READ_ONLY",
        policy_action="filter_recipient",
    ),
    _spec(
        "query",
        "query",
        "transaction_list",
        QueryTaskParameters,
        TransactionResult,
        "READ_ONLY",
        policy_action="filter_recipient",
    ),
    _spec(
        "query",
        "query",
        "transaction_detail",
        QueryTaskParameters,
        TransactionResult,
        "READ_ONLY",
        policy_action="filter_recipient",
    ),
    _spec(
        "query",
        "query",
        "beneficiary_summary",
        QueryTaskParameters,
        TransactionResult,
        "READ_ONLY",
        policy_action="aggregate_group",
    ),
    _spec(
        "query",
        "query",
        "update_query_preferences",
        QueryPreferenceTaskParameters,
        TransactionResult,
        "MUTATION",
    ),
    _spec(
        "support",
        "support",
        "handle_request",
        SupportTaskParameters,
        SupportResult,
        "READ_ONLY",
        policy_action="collect_details",
    ),
    _spec(
        "support",
        "support",
        "report_issue",
        SupportTaskParameters,
        SupportResult,
        "MUTATION",
        policy_action="collect_details",
    ),
    _spec(
        "support",
        "support",
        "list_support_tickets",
        SupportTaskParameters,
        SupportResult,
        "READ_ONLY",
        policy_action="lookup_ticket",
    ),
    _spec(
        "support",
        "support",
        "find_support_ticket",
        SupportTaskParameters,
        SupportResult,
        "READ_ONLY",
        policy_action="lookup_ticket",
    ),
    _spec(
        "support",
        "support",
        "append_support_ticket_note",
        SupportTaskParameters,
        SupportResult,
        "MUTATION",
        policy_action="update_ticket",
    ),
    _spec(
        "support",
        "support",
        "close_support_ticket",
        SupportTaskParameters,
        SupportResult,
        "MUTATION",
        confirmation=True,
        policy_action="close_ticket",
    ),
    _spec("faq", "faq", "answer_question", SupportTaskParameters, FAQResult, "READ_ONLY"),
    _spec(
        "orchestrator",
        "orchestrator",
        "resume_session",
        EmptyTaskParameters,
        TransactionResult,
        "MUTATION",
        policy_action="resume_session",
    ),
    _spec(
        "orchestrator",
        "orchestrator",
        "dismiss_resume_session",
        EmptyTaskParameters,
        TransactionResult,
        "MUTATION",
        policy_action="dismiss_resume_session",
    ),
)

WORKER_OPERATIONS: dict[tuple[str, str], WorkerOperationSpec] = {
    (spec.executor, spec.action): spec for spec in _OPERATIONS
}

DEFAULT_OPERATION_BY_EXECUTOR: dict[str, str] = {
    "transfer": "send_money",
    "account": "list_accounts",
    "beneficiary": "list_beneficiaries",
    "airtime": "buy_airtime",
    "query": "transaction_search",
    "data": "buy_data",
    "faq": "answer_question",
    "support": "handle_request",
    "schedule": "list_scheduled_transactions",
    "orchestrator": "resume_session",
}


def operation_spec(executor: str, action: str) -> WorkerOperationSpec:
    """Return the registered operation or raise a stable contract error."""

    key = (str(executor).strip().lower(), str(action).strip().lower())
    try:
        return WORKER_OPERATIONS[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported worker operation: executor={key[0]!r}, action={key[1]!r}") from exc


def operations_for_domain(domain: str) -> tuple[WorkerOperationSpec, ...]:
    return tuple(spec for spec in _OPERATIONS if spec.domain == domain)


__all__ = [
    "DEFAULT_OPERATION_BY_EXECUTOR",
    "OperationDomain",
    "OperationRisk",
    "WORKER_OPERATIONS",
    "WorkerOperationSpec",
    "operation_spec",
    "operations_for_domain",
]
