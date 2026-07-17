"""Small LLM-facing planner contracts and downstream adapters."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.money import MoneyAmount
from shared.types.balance import initial_balance_contract
from shared.types.conversation_sets import (
    AccountLifecycleContract,
    AccountLifecycleOperation,
    BeneficiaryOperation,
    BeneficiaryQueryContract,
    ScheduleOperation,
    ScheduleQueryContract,
)
from shared.types.planner import PlannerClauseIntentFamily, PlannerOutput, PlannerResponseKey
from shared.types.read import ReadRequest, ReadSubject, ResponseShape


def _strip_annotations(schema: dict[str, Any]) -> None:
    root_title = schema.get("title")

    def strip(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("title", None)
            value.pop("description", None)
            value.pop("default", None)
            for child in value.values():
                strip(child)
        elif isinstance(value, list):
            for child in value:
                strip(child)

    strip(schema)
    # LangChain uses the root schema title as the function/tool name. Nested
    # titles are prompt bloat; the root title is part of the provider contract.
    if isinstance(root_title, str) and root_title:
        schema["title"] = root_title


class _KnownPlanBase(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_strip_annotations)

    primary_intent: Literal["transfer", "airtime", "data", "mixed"]
    language: str | None = None
    normalized_instruction: str = ""
    clauses: list[PlannerLLMClause] = Field(default_factory=list)


class PlannerLLMClause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ids: list[str] = Field(default_factory=list)


class PlannerLLMTransferParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: str | MoneyAmount | None = None
    transfer_all: bool = False
    transfer_percentage: float | None = None
    recipient_name: str | None = None
    recipient_allocations: list[PlannerLLMRecipientAllocation] | None = None
    recipient_account: str | None = None
    bank_name: str | None = None
    narration: str | None = None
    source_bank_name: str | None = None
    source_account_index: int | None = None
    source_accounts: list[str] | None = None
    use_dual_accounts: bool | None = None
    schedule: str | None = None
    recurring: bool | None = None


class PlannerLLMAirtimeParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: str | MoneyAmount | None = None
    recipient_name: str | None = None
    recipient_phone: str | None = None
    network: str | None = None
    is_self: bool = False
    source_bank_name: str | None = None
    source_account_index: int | None = None
    schedule: str | None = None
    recurring: bool | None = None


class PlannerLLMDataParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: str | MoneyAmount | None = None
    recipient_name: str | None = None
    recipient_phone: str | None = None
    network: str | None = None
    budget: str | None = None
    plan: str | None = None
    size_preference: str | None = None
    validity_preference: str | None = None
    is_self: bool = False
    source_bank_name: str | None = None
    source_account_index: int | None = None
    schedule: str | None = None
    recurring: bool | None = None


class PlannerLLMRecipientAllocation(BaseModel):
    """A recipient-specific amount for a compact bulk-transfer plan."""

    model_config = ConfigDict(extra="forbid")

    recipient_name: str
    amount: str | MoneyAmount
    recipient_account: str | None = None
    bank_name: str | None = None


class PlannerLLMTransactionParameters(BaseModel):
    """Typed superset used only when a known turn spans transaction executors."""

    model_config = ConfigDict(extra="forbid")

    amount: str | MoneyAmount | None = None
    recipient_name: str | None = None
    recipient_allocations: list[PlannerLLMRecipientAllocation] | None = None
    recipient_account: str | None = None
    bank_name: str | None = None
    recipient_phone: str | None = None
    network: str | None = None
    is_self: bool = False
    source_bank_name: str | None = None
    source_account_index: int | None = None
    schedule: str | None = None
    recurring: bool | None = None


class PlannerLLMGenericParameters(PlannerLLMTransactionParameters):
    """Bounded parameters for ambiguous routes without free-form JSON objects."""

    query: str | None = None
    account_id: str | None = None
    alias: str | None = None
    read_subject: ReadSubject | None = None
    response_shape: ResponseShape | None = None
    entity_name: str | None = None
    read_status: str | None = None


def _adapt_generic_read_task(task: dict[str, Any]) -> bool:
    """Materialize compact LLM read fields into canonical worker contracts."""
    parameters = task.get("parameters")
    if not isinstance(parameters, dict):
        return True
    subject = parameters.pop("read_subject", None)
    shape = parameters.pop("response_shape", None)
    entity_name = parameters.pop("entity_name", None)
    status = parameters.pop("read_status", None)
    if subject is None and shape is None:
        return True
    if not isinstance(subject, str) or not isinstance(shape, str):
        return False

    executor = str(task.get("executor") or "")
    expected_executor = {
        "balance": "account",
        "linked_account": "account",
        "default_account": "account",
        "beneficiary": "beneficiary",
        "schedule": "schedule",
        "ticket": "support",
        "receipt": "support",
        "transaction": "query",
    }.get(subject)
    if executor != expected_executor:
        return False
    bank_name = parameters.get("bank_name")
    try:
        request = ReadRequest.model_validate(
            {
                "subject": subject,
                "response_shape": shape,
                "entity_name": entity_name,
                "bank_name": bank_name,
                "status": status,
            }
        )
    except ValueError:
        return False
    subject = request.subject
    shape = request.response_shape
    parameters["read_request"] = request.model_dump(mode="json", exclude_none=True)

    if subject == "beneficiary":
        beneficiary_operation: BeneficiaryOperation = "list"
        if shape == "fact_bool":
            beneficiary_operation = "existence"
        elif shape == "fact_count":
            beneficiary_operation = "count"
        elif shape == "surface_detail":
            beneficiary_operation = "detail"
        parameters["beneficiary_contract"] = BeneficiaryQueryContract(
            operation=beneficiary_operation,
            response_shape=shape,
            entity_name=entity_name,
            bank_name=bank_name,
        ).model_dump(mode="json", exclude_none=True)
        task["action"] = "list_beneficiaries"
    elif subject == "schedule":
        schedule_operation: ScheduleOperation = "list"
        if shape == "fact_bool":
            schedule_operation = "existence"
        elif shape == "fact_count":
            schedule_operation = "count"
        elif shape in {"fact_status", "surface_detail"}:
            schedule_operation = "detail"
        parameters["schedule_contract"] = ScheduleQueryContract(
            operation=schedule_operation,
            response_shape=shape,
            recipient_name=entity_name,
            statuses=[status] if status else [],
        ).model_dump(mode="json", exclude_none=True)
        task["action"] = "list_scheduled_transactions"
    elif subject in {"linked_account", "default_account"}:
        lifecycle_operation: AccountLifecycleOperation = (
            "default_identity" if subject == "default_account" else "list"
        )
        if subject == "linked_account":
            if shape == "fact_bool":
                lifecycle_operation = "existence"
            elif shape == "fact_count":
                lifecycle_operation = "count"
            elif shape == "fact_status":
                lifecycle_operation = "readiness"
            elif shape == "surface_detail":
                lifecycle_operation = "detail"
        parameters["account_lifecycle_contract"] = AccountLifecycleContract(
            operation=lifecycle_operation,
            response_shape=shape,
            bank_name=bank_name,
            mandate_statuses=[status] if status else [],
        ).model_dump(mode="json", exclude_none=True)
        if subject == "default_account":
            task["action"] = "get_default"
        elif shape in {"fact_bool", "fact_count"}:
            task["action"] = "count"
        else:
            task["action"] = "list_accounts"
    elif subject == "balance":
        parameters["balance_contract"] = initial_balance_contract(
            bank_name=bank_name,
            response_shape=shape,
        ).model_dump(mode="json", exclude_none=True)
        task["action"] = "check_balance"
    return True


class _PlannerLLMTaskBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    instruction: str
    source_clause_index: int | None = None


class PlannerLLMTransferTask(_PlannerLLMTaskBase):
    action: Literal["send_money", "schedule_transfer", "recurring_transfer"]
    parameters: PlannerLLMTransferParameters = Field(default_factory=PlannerLLMTransferParameters)


class PlannerLLMAirtimeTask(_PlannerLLMTaskBase):
    action: Literal["buy_airtime", "schedule_airtime", "recurring_airtime"]
    parameters: PlannerLLMAirtimeParameters = Field(default_factory=PlannerLLMAirtimeParameters)


class PlannerLLMDataTask(_PlannerLLMTaskBase):
    action: Literal["buy_data", "schedule_data", "recurring_data"]
    parameters: PlannerLLMDataParameters = Field(default_factory=PlannerLLMDataParameters)


class PlannerLLMGenericTask(_PlannerLLMTaskBase):
    executor: Literal[
        "transfer",
        "airtime",
        "data",
        "query",
        "account",
        "beneficiary",
        "schedule",
        "support",
        "faq",
        "orchestrator",
    ]
    action: str
    parameters: PlannerLLMGenericParameters = Field(default_factory=PlannerLLMGenericParameters)


class PlannerLLMTransactionTask(_PlannerLLMTaskBase):
    executor: Literal["transfer", "airtime", "data"]
    action: Literal[
        "send_money",
        "schedule_transfer",
        "recurring_transfer",
        "buy_airtime",
        "schedule_airtime",
        "recurring_airtime",
        "buy_data",
        "schedule_data",
        "recurring_data",
    ]
    parameters: PlannerLLMTransactionParameters = Field(default_factory=PlannerLLMTransactionParameters)


class PlannerKnownTransferPlan(_KnownPlanBase):
    tasks: list[PlannerLLMTransferTask] = Field(default_factory=list)


class PlannerKnownAirtimePlan(_KnownPlanBase):
    tasks: list[PlannerLLMAirtimeTask] = Field(default_factory=list)


class PlannerKnownDataPlan(_KnownPlanBase):
    tasks: list[PlannerLLMDataTask] = Field(default_factory=list)


class PlannerKnownTransferAirtimePlan(_KnownPlanBase):
    tasks: list[PlannerLLMTransactionTask] = Field(default_factory=list)


class PlannerKnownTransferDataPlan(_KnownPlanBase):
    tasks: list[PlannerLLMTransactionTask] = Field(default_factory=list)


class PlannerKnownAirtimeDataPlan(_KnownPlanBase):
    tasks: list[PlannerLLMTransactionTask] = Field(default_factory=list)


class PlannerKnownTransactionsPlan(_KnownPlanBase):
    tasks: list[PlannerLLMTransactionTask] = Field(default_factory=list)


class PlannerAmbiguousPlan(_KnownPlanBase):
    """Bounded planner output for turns that may answer instead of dispatch."""

    response_key: PlannerResponseKey | None = None
    response: str = ""
    confidence: float = Field(default=0.9, ge=0, le=1)
    unsupported_capability: str | None = None
    tasks: list[PlannerLLMGenericTask] = Field(default_factory=list)


PlannerLLMOutput = (
    PlannerKnownTransferPlan
    | PlannerKnownAirtimePlan
    | PlannerKnownDataPlan
    | PlannerKnownTransferAirtimePlan
    | PlannerKnownTransferDataPlan
    | PlannerKnownAirtimeDataPlan
    | PlannerKnownTransactionsPlan
    | PlannerAmbiguousPlan
)


def adapt_planner_llm_output(value: BaseModel, *, original_text: str) -> PlannerOutput:
    """Restore the stable rich PlannerOutput contract using deterministic defaults."""
    # Sparse function-call arguments must stay sparse. Superset task schemas
    # contain fields belonging to sibling executors; dumping defaults would
    # leak those fields into the strict downstream executor contracts.
    payload = value.model_dump(exclude_none=True, exclude_defaults=True)
    tasks = payload.get("tasks") or []
    executor_by_task_id = {
        str(task.get("task_id")): str(task.get("executor"))
        for task in tasks
        if isinstance(task, dict) and task.get("task_id") and task.get("executor")
    }
    tasks = [task for task in tasks if isinstance(task, dict) and _adapt_generic_read_task(task)]
    payload["tasks"] = tasks
    for task in tasks:
        if not isinstance(task, dict):
            continue
        executor = task.pop("executor", None)
        money_move = executor in {"transfer", "airtime", "data"} or str(task.get("action", "")).startswith(
            ("send_", "buy_", "schedule_", "recurring_")
        )
        task.setdefault("risk", "MONEY_MOVE" if money_move else "READ_ONLY")
        task.setdefault("depends_on", [])
    clauses = payload.get("clauses") or []
    for clause_index, clause in enumerate(clauses, start=1):
        if isinstance(clause, dict):
            clause_task_ids = set(clause.get("task_ids") or [])
            clause_executors = {
                executor_by_task_id[task_id] for task_id in clause_task_ids if task_id in executor_by_task_id
            }
            inferred_family: PlannerClauseIntentFamily = "unknown"
            if clause_executors == {"transfer"}:
                inferred_family = "transfer"
            elif clause_executors == {"airtime"}:
                inferred_family = "airtime"
            elif clause_executors == {"data"}:
                inferred_family = "data"
            clause["clause_index"] = clause_index
            clause["intent_family"] = inferred_family
            clause["text"] = " and ".join(
                str(task.get("instruction") or "").strip()
                for task in tasks
                if isinstance(task, dict) and task.get("task_id") in clause_task_ids and task.get("instruction")
            )
            clause["text"] = clause["text"] or original_text
            clause.setdefault("extracted_fields", {})
    primary_intent = str(payload.get("primary_intent") or "unknown")
    response_key = payload.get("response_key")
    payload.update(
        {
            "primary_intent": primary_intent,
            "response": str(payload.get("response") or ""),
            "response_key": response_key,
            "confidence": float(payload.get("confidence") or 0.9),
            "is_complex": len(tasks) > 1 or len(clauses) > 1 or primary_intent == "mixed",
            "is_cancellation": primary_intent == "cancel" or response_key == "planner.cancelled",
            "is_confirmation": False,
            "normalized_instruction": str(payload.get("normalized_instruction") or original_text).strip(),
            "tasks": tasks,
            "clauses": clauses,
        }
    )
    payload["detected_language"] = payload.pop("language", None)
    return PlannerOutput.model_validate(payload)


__all__ = [
    "PlannerAmbiguousPlan",
    "PlannerKnownAirtimeDataPlan",
    "PlannerKnownAirtimePlan",
    "PlannerKnownDataPlan",
    "PlannerKnownTransactionsPlan",
    "PlannerKnownTransferAirtimePlan",
    "PlannerKnownTransferDataPlan",
    "PlannerKnownTransferPlan",
    "adapt_planner_llm_output",
]
