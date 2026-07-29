"""Direct task construction for gate-owned shortcuts."""

from typing import Any, Literal

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.utils.task_payload_schedule import (
    derive_transfer_schedule_fields,
    infer_schedule_action_from_text,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.routing import DIRECT_DOMAIN_ACTIONS
from apps.chat.src.agent.orchestrator.workflows.gate.state.locale_state import _current_locale
from apps.chat.src.agent.orchestrator.workflows.gate.state.state_view import GateStateView
from banking.policy.service import capability_block_message
from shared.types.balance import BalanceConversationState, BalanceQueryContract
from shared.types.conversation_sets import (
    AccountLifecycleContract,
    BeneficiaryQueryContract,
    ScheduleQueryContract,
)
from shared.types.planner import QueryInsightType
from shared.types.read import ReadRequest


def _next_direct_account_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "direct_account_balance"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_account_balance_{idx}"
    return task_id


def _next_direct_query_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "direct_query"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_query_{idx}"
    return task_id


def _next_direct_domain_task_id(existing_tasks: dict[str, TaskSpec], domain: str) -> str:
    idx = 1
    task_id = f"direct_{domain}"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_{domain}_{idx}"
    return task_id


def _next_direct_beneficiary_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "direct_beneficiary_save"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_beneficiary_save_{idx}"
    return task_id


def _build_direct_domain_task(
    *,
    state_view: GateStateView,
    domain: Literal["query", "account", "support", "beneficiary", "transfer", "airtime", "data", "schedule", "faq"],
    mode: str | None = None,
    message_text: str | None = None,
    read_request: ReadRequest | None = None,
    balance_contract: BalanceQueryContract | None = None,
    beneficiary_contract: BeneficiaryQueryContract | None = None,
    schedule_contract: ScheduleQueryContract | None = None,
    account_lifecycle_contract: AccountLifecycleContract | None = None,
    query_insight_type: QueryInsightType | None = None,
) -> tuple[str, TaskSpec]:
    if domain == "query":
        task_id = _next_direct_query_task_id(state_view.tasks)
    else:
        task_id = _next_direct_domain_task_id(state_view.tasks, domain)

    task_message = state_view.last_message_text if message_text is None else message_text
    payload: dict[str, Any] = {
        "message": task_message,
        "instruction": task_message,
    }
    if read_request is not None:
        payload["read_request"] = read_request.model_dump(mode="json", exclude_none=True)
        required_contract = {
            "balance": balance_contract,
            "beneficiary": beneficiary_contract,
            "schedule": schedule_contract,
            "linked_account": account_lifecycle_contract,
            "default_account": account_lifecycle_contract,
        }.get(read_request.subject, True)
        if required_contract is None:
            raise ValueError(f"{read_request.subject} reads require their specialized contract")
    specialized_contracts = (
        ("beneficiary_contract", beneficiary_contract, "beneficiary"),
        ("schedule_contract", schedule_contract, "schedule"),
        ("account_lifecycle_contract", account_lifecycle_contract, "linked_account"),
    )
    for key, contract, subject in specialized_contracts:
        if contract is not None and read_request is not None and (
            read_request.subject == subject
            or (key == "account_lifecycle_contract" and read_request.subject == "default_account")
        ):
            payload[key] = contract.model_dump(mode="json", exclude_none=True)
    if read_request is None or read_request.subject != "balance":
        balance_contract = None
    if balance_contract is not None:
        payload["balance_contract"] = balance_contract.model_dump(mode="json", exclude_none=True)
        payload["balance_conversation_state"] = BalanceConversationState(
            focused_bank=(balance_contract.bank_names[-1] if len(balance_contract.bank_names) == 1 else None),
            mentioned_banks=balance_contract.bank_names,
            last_result_banks=balance_contract.bank_names,
            last_operation=balance_contract.operation,
        ).model_dump(mode="json", exclude_none=True)
    if domain == "query":
        if mode == "new":
            payload["force_new_query"] = True
        if query_insight_type is not None:
            payload["query_insight_type"] = query_insight_type
    elif domain == "account" and read_request is not None:
        if read_request.subject == "balance":
            payload["action"] = "check_balance"
            if balance_contract is not None and balance_contract.account_scope == "named":
                payload["identifiers"] = balance_contract.bank_names
                if len(balance_contract.bank_names) == 1:
                    payload["identifier"] = balance_contract.bank_names[0]
            elif read_request.bank_name:
                payload["identifier"] = read_request.bank_name
            payload["skip_parse"] = True
        elif read_request.subject == "default_account":
            payload["action"] = "get_default"
            payload["skip_parse"] = True
        elif read_request.subject == "linked_account":
            payload["action"] = (
                "count"
                if read_request.response_shape in {"fact_count", "fact_bool"}
                else "list_accounts"
            )
            if read_request.bank_name:
                payload["identifier"] = read_request.bank_name
            payload["skip_parse"] = True
    elif domain in {"transfer", "airtime", "data"}:
        schedule_message_text = task_message or ""
        inferred_schedule_action = infer_schedule_action_from_text(schedule_message_text)
        if inferred_schedule_action:
            domain_schedule_action = {
                "transfer": inferred_schedule_action,
                "airtime": "recurring_airtime"
                if inferred_schedule_action == "recurring_transfer"
                else "schedule_airtime",
                "data": "recurring_data" if inferred_schedule_action == "recurring_transfer" else "schedule_data",
            }[domain]
            payload["action"] = domain_schedule_action
            payload.update(
                derive_transfer_schedule_fields(
                    schedule_message_text,
                    schedule_text=None,
                    scheduled_text=None,
                    recurring_flag=domain_schedule_action.startswith("recurring_"),
                )
            )
    elif domain == "beneficiary":
        payload["action"] = "list_beneficiaries"
        payload["intent"] = "list_beneficiaries"
        payload["list_intent"] = True
        if read_request is not None and read_request.entity_name:
            payload["name_filter"] = read_request.entity_name
    elif domain == "schedule":
        payload["action"] = (
            "list_scheduled_runs"
            if schedule_contract is not None and schedule_contract.surface == "runs"
            else "list_scheduled_transactions"
        )
    elif domain == "support" and read_request is not None and read_request.subject == "ticket":
        payload["action"] = (
            "find_support_ticket"
            if read_request.response_shape in {"surface_detail", "fact_status"}
            else "list_support_tickets"
        )
        payload["ticket_code"] = read_request.reference
    elif domain == "faq":
        payload["action"] = "answer_question"

    spec = TaskSpec(
        id=task_id,
        type=domain,
        stage=TaskStage.DRAFT,
        payload=payload,
    )
    return task_id, spec


def _direct_domain_capability_block_message(
    state_view: GateStateView,
    domain: str,
) -> str | None:
    action = DIRECT_DOMAIN_ACTIONS.get(domain)
    if not action:
        return None
    return capability_block_message(domain=domain, action=action, locale=_current_locale(state_view))
