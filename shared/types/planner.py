"""Models for task planning and normalization."""

from collections.abc import Iterable
from typing import Annotated, Any, ClassVar, Literal, TypeAlias, cast, get_args

from pydantic import BaseModel, ConfigDict, Field

from shared.money import MoneyAmount


class ContextReference(BaseModel):
    """Pointer to a context entity."""

    selector: Literal["previous", "index", "label"]
    index: int | None = None
    label: str | None = None


class RecipientAllocation(BaseModel):
    """Recipient-side transfer allocation."""

    recipient_name: str = Field(..., description="Recipient/beneficiary name exactly as referenced by the user")
    amount: MoneyAmount = Field(..., gt=0, description="Allocated amount for this recipient")


class FundingSplitUpdate(BaseModel):
    """Source-side funding split for a pending transfer confirmation edit."""

    model_config = ConfigDict(extra="forbid")

    bank_name: str = Field(..., description="User source bank/account reference for this funding leg")
    amount: MoneyAmount = Field(..., gt=0, description="Amount to fund from this source account")


ResponseShape: TypeAlias = Literal[
    "fact_bool",
    "fact_count",
    "fact_status",
    "fact_recap",
    "surface_list",
    "surface_detail",
    "surface_paginated",
    "surface_actionable",
]


class BaseTaskParameters(BaseModel):
    """Base class for executor-specific planner task parameter contracts."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "description": "Sparse executor-specific parameters. Use only fields valid for the task executor.",
            "additionalProperties": True,
        },
    )


class EmptyTaskParameters(BaseTaskParameters):
    """No-slot task parameters for conversational/orchestrator tasks."""

    model_config = ConfigDict(extra="forbid")


class TransferTaskParameters(BaseTaskParameters):
    """Transfer task parameters."""

    model_config = ConfigDict(extra="forbid")

    amount: str | MoneyAmount | None = None
    transfer_all: bool = False
    transfer_percentage: float | None = None
    recipient: str | None = None
    recipient_name: str | None = None
    narration: str | None = None
    recipient_phone: str | None = None
    recipient_account: str | None = None
    bank_name: str | None = None
    schedule: str | None = None
    scheduled: str | None = None
    recurring: bool | None = None
    schedule_id: str | None = None
    schedule_selector: str | None = None
    international: bool | None = None
    alias: str | None = None
    reference: ContextReference | None = None
    source_bank_name: str | None = None
    source_account_index: int | None = None
    use_dual_accounts: bool | None = None
    source_accounts: list[str] | None = None
    explicit_split: dict[str, MoneyAmount] | None = None
    recipient_allocations: list[RecipientAllocation] | None = None
    recipient_binding_source: Literal["fanout"] | None = None
    recipient_binding_index: int | None = None


class AirtimeTaskParameters(BaseTaskParameters):
    """Airtime task parameters."""

    model_config = ConfigDict(extra="forbid")

    amount: str | MoneyAmount | None = None
    recipient_name: str | None = None
    narration: str | None = None
    recipient_phone: str | None = None
    phone: str | None = None
    target_phone: str | None = None
    network: str | None = None
    is_self: bool = False
    schedule: str | None = None
    scheduled: str | None = None
    recurring: bool | None = None
    schedule_id: str | None = None
    schedule_selector: str | None = None
    source_bank_name: str | None = None
    source_account_index: int | None = None


class DataTaskParameters(BaseTaskParameters):
    """Data purchase task parameters."""

    model_config = ConfigDict(extra="forbid")

    amount: str | MoneyAmount | None = None
    recipient_name: str | None = None
    recipient_phone: str | None = None
    phone: str | None = None
    target_phone: str | None = None
    network: str | None = None
    budget: str | None = None
    plan: str | None = None
    plan_name: str | None = None
    size_preference: str | None = None
    validity_preference: str | None = None
    selection_preference: str | None = None
    usage_intent: str | None = None
    is_self: bool = False
    schedule: str | None = None
    scheduled: str | None = None
    recurring: bool | None = None
    schedule_id: str | None = None
    schedule_selector: str | None = None
    source_bank_name: str | None = None
    source_account_index: int | None = None


class AccountTaskParameters(BaseTaskParameters):
    """Account-management and account-read parameters."""

    model_config = ConfigDict(extra="forbid")

    response_shape: ResponseShape | None = None
    bank_name: str | None = None
    source_bank_name: str | None = None
    source_account_index: int | None = None
    alias: str | None = None


class BeneficiaryTaskParameters(BaseTaskParameters):
    """Beneficiary task parameters."""

    model_config = ConfigDict(extra="forbid")

    response_shape: ResponseShape | None = None
    recipient: str | None = None
    recipient_name: str | None = None
    recipient_account: str | None = None
    bank_name: str | None = None
    phone: str | None = None
    target_phone: str | None = None
    alias: str | None = None


class ScheduleTaskParameters(BaseTaskParameters):
    """Scheduled-transaction management parameters."""

    model_config = ConfigDict(extra="forbid")

    response_shape: ResponseShape | None = None
    amount: str | MoneyAmount | None = None
    recipient_name: str | None = None
    recipient_phone: str | None = None
    phone: str | None = None
    network: str | None = None
    plan: str | None = None
    plan_name: str | None = None
    schedule: str | None = None
    scheduled: str | None = None
    recurring: bool | None = None
    schedule_id: str | None = None
    schedule_selector: str | None = None
    schedule_response_mode: Literal["list", "count"] | None = None
    source_bank_name: str | None = None
    source_account_index: int | None = None


class QueryTaskParameters(BaseTaskParameters):
    """Read-only query task parameters."""

    model_config = ConfigDict(extra="forbid")

    response_shape: ResponseShape | None = None
    schedule_response_mode: Literal["list", "count"] | None = None


class SupportTaskParameters(BaseTaskParameters):
    """Support/FAQ task parameters."""

    model_config = ConfigDict(extra="forbid")

    response_shape: ResponseShape | None = None


PlannerTaskParameters: TypeAlias = (
    TransferTaskParameters
    | AirtimeTaskParameters
    | DataTaskParameters
    | AccountTaskParameters
    | BeneficiaryTaskParameters
    | ScheduleTaskParameters
    | QueryTaskParameters
    | SupportTaskParameters
    | EmptyTaskParameters
)


TaskRisk: TypeAlias = Literal["READ_ONLY", "MUTATION", "MONEY_MOVE"]
PlannerExecutor: TypeAlias = Literal[
    "transfer",
    "query",
    "airtime",
    "data",
    "account",
    "support",
    "faq",
    "beneficiary",
    "schedule",
    "orchestrator",
]


class BasePlannedTask(BaseModel):
    """Shared fields for action-specific planner task contracts."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., description="Stable ID referenced by depends_on")
    instruction: str
    description: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    condition: str | None = None
    risk: TaskRisk = "READ_ONLY"
    idempotency_key: str | None = None  # Set by engine
    source_clause_index: int | None = Field(
        default=None,
        description="1-based clause index in planner decomposition that produced this task",
    )


class SendMoneyTask(BasePlannedTask):
    action: Literal["send_money"]
    executor: ClassVar[Literal["transfer"]] = "transfer"
    parameters: TransferTaskParameters = Field(default_factory=TransferTaskParameters)


class ScheduleTransferTask(BasePlannedTask):
    action: Literal["schedule_transfer"]
    executor: ClassVar[Literal["transfer"]] = "transfer"
    parameters: TransferTaskParameters = Field(default_factory=TransferTaskParameters)


class RecurringTransferTask(BasePlannedTask):
    action: Literal["recurring_transfer"]
    executor: ClassVar[Literal["transfer"]] = "transfer"
    parameters: TransferTaskParameters = Field(default_factory=TransferTaskParameters)


class BuyAirtimeTask(BasePlannedTask):
    action: Literal["buy_airtime"]
    executor: ClassVar[Literal["airtime"]] = "airtime"
    parameters: AirtimeTaskParameters = Field(default_factory=AirtimeTaskParameters)


class ScheduleAirtimeTask(BasePlannedTask):
    action: Literal["schedule_airtime"]
    executor: ClassVar[Literal["airtime"]] = "airtime"
    parameters: AirtimeTaskParameters = Field(default_factory=AirtimeTaskParameters)


class RecurringAirtimeTask(BasePlannedTask):
    action: Literal["recurring_airtime"]
    executor: ClassVar[Literal["airtime"]] = "airtime"
    parameters: AirtimeTaskParameters = Field(default_factory=AirtimeTaskParameters)


class BuyDataTask(BasePlannedTask):
    action: Literal["buy_data"]
    executor: ClassVar[Literal["data"]] = "data"
    parameters: DataTaskParameters = Field(default_factory=DataTaskParameters)


class ScheduleDataTask(BasePlannedTask):
    action: Literal["schedule_data"]
    executor: ClassVar[Literal["data"]] = "data"
    parameters: DataTaskParameters = Field(default_factory=DataTaskParameters)


class RecurringDataTask(BasePlannedTask):
    action: Literal["recurring_data"]
    executor: ClassVar[Literal["data"]] = "data"
    parameters: DataTaskParameters = Field(default_factory=DataTaskParameters)


class ListScheduledTransactionsTask(BasePlannedTask):
    action: Literal["list_scheduled_transactions"]
    executor: ClassVar[Literal["schedule"]] = "schedule"
    parameters: ScheduleTaskParameters = Field(default_factory=ScheduleTaskParameters)


class FindScheduledTransactionTask(BasePlannedTask):
    action: Literal["find_scheduled_transaction"]
    executor: ClassVar[Literal["schedule"]] = "schedule"
    parameters: ScheduleTaskParameters = Field(default_factory=ScheduleTaskParameters)


class CancelScheduledTransactionTask(BasePlannedTask):
    action: Literal["cancel_scheduled_transaction"]
    executor: ClassVar[Literal["schedule"]] = "schedule"
    parameters: ScheduleTaskParameters = Field(default_factory=ScheduleTaskParameters)


class EditScheduledTransactionTask(BasePlannedTask):
    action: Literal["edit_scheduled_transaction"]
    executor: ClassVar[Literal["schedule"]] = "schedule"
    parameters: ScheduleTaskParameters = Field(default_factory=ScheduleTaskParameters)


class CheckBalanceTask(BasePlannedTask):
    action: Literal["check_balance"]
    executor: ClassVar[Literal["account"]] = "account"
    parameters: AccountTaskParameters = Field(default_factory=AccountTaskParameters)


class ListAccountsTask(BasePlannedTask):
    action: Literal["list_accounts"]
    executor: ClassVar[Literal["account"]] = "account"
    parameters: AccountTaskParameters = Field(default_factory=AccountTaskParameters)


class CountAccountsTask(BasePlannedTask):
    action: Literal["count"]
    executor: ClassVar[Literal["account"]] = "account"
    parameters: AccountTaskParameters = Field(default_factory=AccountTaskParameters)


class LinkAccountTask(BasePlannedTask):
    action: Literal["link"]
    executor: ClassVar[Literal["account"]] = "account"
    parameters: AccountTaskParameters = Field(default_factory=AccountTaskParameters)


class UnlinkAccountTask(BasePlannedTask):
    action: Literal["unlink"]
    executor: ClassVar[Literal["account"]] = "account"
    parameters: AccountTaskParameters = Field(default_factory=AccountTaskParameters)


class SetDefaultAccountTask(BasePlannedTask):
    action: Literal["set_default"]
    executor: ClassVar[Literal["account"]] = "account"
    parameters: AccountTaskParameters = Field(default_factory=AccountTaskParameters)


class OverallBalanceTask(BasePlannedTask):
    action: Literal["overall_balance"]
    executor: ClassVar[Literal["account"]] = "account"
    parameters: AccountTaskParameters = Field(default_factory=AccountTaskParameters)


class ListBeneficiariesTask(BasePlannedTask):
    action: Literal["list_beneficiaries"]
    executor: ClassVar[Literal["beneficiary"]] = "beneficiary"
    parameters: BeneficiaryTaskParameters = Field(default_factory=BeneficiaryTaskParameters)


class AddBeneficiaryTask(BasePlannedTask):
    action: Literal["add_beneficiary"]
    executor: ClassVar[Literal["beneficiary"]] = "beneficiary"
    parameters: BeneficiaryTaskParameters = Field(default_factory=BeneficiaryTaskParameters)


class DeleteBeneficiaryTask(BasePlannedTask):
    action: Literal["delete_beneficiary"]
    executor: ClassVar[Literal["beneficiary"]] = "beneficiary"
    parameters: BeneficiaryTaskParameters = Field(default_factory=BeneficiaryTaskParameters)


class UpdateBeneficiaryTask(BasePlannedTask):
    action: Literal["update_beneficiary"]
    executor: ClassVar[Literal["beneficiary"]] = "beneficiary"
    parameters: BeneficiaryTaskParameters = Field(default_factory=BeneficiaryTaskParameters)


class SaveBeneficiaryTask(BasePlannedTask):
    action: Literal["save_beneficiary"]
    executor: ClassVar[Literal["beneficiary"]] = "beneficiary"
    parameters: BeneficiaryTaskParameters = Field(default_factory=BeneficiaryTaskParameters)


class TransactionSearchTask(BasePlannedTask):
    action: Literal["transaction_search"]
    executor: ClassVar[Literal["query"]] = "query"
    parameters: QueryTaskParameters = Field(default_factory=QueryTaskParameters)


class TransactionListTask(BasePlannedTask):
    action: Literal["transaction_list"]
    executor: ClassVar[Literal["query"]] = "query"
    parameters: QueryTaskParameters = Field(default_factory=QueryTaskParameters)


class BeneficiarySummaryTask(BasePlannedTask):
    action: Literal["beneficiary_summary"]
    executor: ClassVar[Literal["query"]] = "query"
    parameters: QueryTaskParameters = Field(default_factory=QueryTaskParameters)


class SupportHandleRequestTask(BasePlannedTask):
    action: Literal["handle_request"]
    executor: ClassVar[Literal["support"]] = "support"
    parameters: SupportTaskParameters = Field(default_factory=SupportTaskParameters)


class SupportReportIssueTask(BasePlannedTask):
    action: Literal["report_issue"]
    executor: ClassVar[Literal["support"]] = "support"
    parameters: SupportTaskParameters = Field(default_factory=SupportTaskParameters)


class FaqAnswerQuestionTask(BasePlannedTask):
    action: Literal["answer_question"]
    executor: ClassVar[Literal["faq"]] = "faq"
    parameters: SupportTaskParameters = Field(default_factory=SupportTaskParameters)


class ResumeSessionTask(BasePlannedTask):
    action: Literal["resume_session"]
    executor: ClassVar[Literal["orchestrator"]] = "orchestrator"
    parameters: EmptyTaskParameters = Field(default_factory=EmptyTaskParameters)


class DismissResumeSessionTask(BasePlannedTask):
    action: Literal["dismiss_resume_session"]
    executor: ClassVar[Literal["orchestrator"]] = "orchestrator"
    parameters: EmptyTaskParameters = Field(default_factory=EmptyTaskParameters)


class TransferPlannedTask(BasePlannedTask):
    action: Literal["send_money", "schedule_transfer", "recurring_transfer"]
    executor: ClassVar[Literal["transfer"]] = "transfer"
    parameters: TransferTaskParameters = Field(default_factory=TransferTaskParameters)


class AirtimePlannedTask(BasePlannedTask):
    action: Literal["buy_airtime", "schedule_airtime", "recurring_airtime"]
    executor: ClassVar[Literal["airtime"]] = "airtime"
    parameters: AirtimeTaskParameters = Field(default_factory=AirtimeTaskParameters)


class DataPlannedTask(BasePlannedTask):
    action: Literal["buy_data", "schedule_data", "recurring_data"]
    executor: ClassVar[Literal["data"]] = "data"
    parameters: DataTaskParameters = Field(default_factory=DataTaskParameters)


class SchedulePlannedTask(BasePlannedTask):
    action: Literal[
        "list_scheduled_transactions",
        "find_scheduled_transaction",
        "cancel_scheduled_transaction",
        "edit_scheduled_transaction",
    ]
    executor: ClassVar[Literal["schedule"]] = "schedule"
    parameters: ScheduleTaskParameters = Field(default_factory=ScheduleTaskParameters)


class AccountPlannedTask(BasePlannedTask):
    action: Literal["check_balance", "list_accounts", "count", "link", "unlink", "set_default", "overall_balance"]
    executor: ClassVar[Literal["account"]] = "account"
    parameters: AccountTaskParameters = Field(default_factory=AccountTaskParameters)


class BeneficiaryPlannedTask(BasePlannedTask):
    action: Literal[
        "list_beneficiaries",
        "add_beneficiary",
        "delete_beneficiary",
        "update_beneficiary",
        "save_beneficiary",
    ]
    executor: ClassVar[Literal["beneficiary"]] = "beneficiary"
    parameters: BeneficiaryTaskParameters = Field(default_factory=BeneficiaryTaskParameters)


class QueryPlannedTask(BasePlannedTask):
    action: Literal["transaction_search", "transaction_list", "beneficiary_summary"]
    executor: ClassVar[Literal["query"]] = "query"
    parameters: QueryTaskParameters = Field(default_factory=QueryTaskParameters)


class SupportPlannedTask(BasePlannedTask):
    action: Literal["handle_request", "report_issue"]
    executor: ClassVar[Literal["support"]] = "support"
    parameters: SupportTaskParameters = Field(default_factory=SupportTaskParameters)


class FaqPlannedTask(BasePlannedTask):
    action: Literal["answer_question"]
    executor: ClassVar[Literal["faq"]] = "faq"
    parameters: SupportTaskParameters = Field(default_factory=SupportTaskParameters)


class OrchestratorPlannedTask(BasePlannedTask):
    action: Literal["resume_session", "dismiss_resume_session"]
    executor: ClassVar[Literal["orchestrator"]] = "orchestrator"
    parameters: EmptyTaskParameters = Field(default_factory=EmptyTaskParameters)


PlannerTaskModel: TypeAlias = type[BasePlannedTask]
PlannerTask: TypeAlias = Annotated[
    TransferPlannedTask
    | AirtimePlannedTask
    | DataPlannedTask
    | SchedulePlannedTask
    | AccountPlannedTask
    | BeneficiaryPlannedTask
    | QueryPlannedTask
    | SupportPlannedTask
    | FaqPlannedTask
    | OrchestratorPlannedTask,
    Field(discriminator="action"),
]
PlannedTask: TypeAlias = PlannerTask

TransferAirtimePlannerTask: TypeAlias = Annotated[
    TransferPlannedTask | AirtimePlannedTask,
    Field(discriminator="action"),
]
TransferDataPlannerTask: TypeAlias = Annotated[
    TransferPlannedTask | DataPlannedTask,
    Field(discriminator="action"),
]
AirtimeDataPlannerTask: TypeAlias = Annotated[
    AirtimePlannedTask | DataPlannedTask,
    Field(discriminator="action"),
]
TransactionalPlannerTask: TypeAlias = Annotated[
    TransferPlannedTask | AirtimePlannedTask | DataPlannedTask,
    Field(discriminator="action"),
]
TransferActionPlannerTask: TypeAlias = Annotated[
    SendMoneyTask | ScheduleTransferTask | RecurringTransferTask,
    Field(discriminator="action"),
]
AirtimeActionPlannerTask: TypeAlias = Annotated[
    BuyAirtimeTask | ScheduleAirtimeTask | RecurringAirtimeTask,
    Field(discriminator="action"),
]
DataActionPlannerTask: TypeAlias = Annotated[
    BuyDataTask | ScheduleDataTask | RecurringDataTask,
    Field(discriminator="action"),
]
TransferAirtimeActionPlannerTask: TypeAlias = Annotated[
    SendMoneyTask
    | ScheduleTransferTask
    | RecurringTransferTask
    | BuyAirtimeTask
    | ScheduleAirtimeTask
    | RecurringAirtimeTask,
    Field(discriminator="action"),
]
TransferDataActionPlannerTask: TypeAlias = Annotated[
    SendMoneyTask | ScheduleTransferTask | RecurringTransferTask | BuyDataTask | ScheduleDataTask | RecurringDataTask,
    Field(discriminator="action"),
]
AirtimeDataActionPlannerTask: TypeAlias = Annotated[
    BuyAirtimeTask | ScheduleAirtimeTask | RecurringAirtimeTask | BuyDataTask | ScheduleDataTask | RecurringDataTask,
    Field(discriminator="action"),
]
TransactionalActionPlannerTask: TypeAlias = Annotated[
    SendMoneyTask
    | ScheduleTransferTask
    | RecurringTransferTask
    | BuyAirtimeTask
    | ScheduleAirtimeTask
    | RecurringAirtimeTask
    | BuyDataTask
    | ScheduleDataTask
    | RecurringDataTask,
    Field(discriminator="action"),
]

_ACTION_TASK_MODELS: dict[str, PlannerTaskModel] = {
    "send_money": TransferPlannedTask,
    "schedule_transfer": TransferPlannedTask,
    "recurring_transfer": TransferPlannedTask,
    "buy_airtime": AirtimePlannedTask,
    "schedule_airtime": AirtimePlannedTask,
    "recurring_airtime": AirtimePlannedTask,
    "buy_data": DataPlannedTask,
    "schedule_data": DataPlannedTask,
    "recurring_data": DataPlannedTask,
    "list_scheduled_transactions": SchedulePlannedTask,
    "find_scheduled_transaction": SchedulePlannedTask,
    "cancel_scheduled_transaction": SchedulePlannedTask,
    "edit_scheduled_transaction": SchedulePlannedTask,
    "check_balance": AccountPlannedTask,
    "list_accounts": AccountPlannedTask,
    "count": AccountPlannedTask,
    "link": AccountPlannedTask,
    "unlink": AccountPlannedTask,
    "set_default": AccountPlannedTask,
    "overall_balance": AccountPlannedTask,
    "list_beneficiaries": BeneficiaryPlannedTask,
    "add_beneficiary": BeneficiaryPlannedTask,
    "delete_beneficiary": BeneficiaryPlannedTask,
    "update_beneficiary": BeneficiaryPlannedTask,
    "save_beneficiary": BeneficiaryPlannedTask,
    "transaction_search": QueryPlannedTask,
    "transaction_list": QueryPlannedTask,
    "beneficiary_summary": QueryPlannedTask,
    "handle_request": SupportPlannedTask,
    "report_issue": SupportPlannedTask,
    "answer_question": FaqPlannedTask,
    "resume_session": OrchestratorPlannedTask,
    "dismiss_resume_session": OrchestratorPlannedTask,
}

_ACTION_PARAMETER_MODELS: dict[str, type[BaseTaskParameters]] = {
    "send_money": TransferTaskParameters,
    "schedule_transfer": TransferTaskParameters,
    "recurring_transfer": TransferTaskParameters,
    "buy_airtime": AirtimeTaskParameters,
    "schedule_airtime": AirtimeTaskParameters,
    "recurring_airtime": AirtimeTaskParameters,
    "buy_data": DataTaskParameters,
    "schedule_data": DataTaskParameters,
    "recurring_data": DataTaskParameters,
    "list_scheduled_transactions": ScheduleTaskParameters,
    "find_scheduled_transaction": ScheduleTaskParameters,
    "cancel_scheduled_transaction": ScheduleTaskParameters,
    "edit_scheduled_transaction": ScheduleTaskParameters,
    "check_balance": AccountTaskParameters,
    "list_accounts": AccountTaskParameters,
    "count": AccountTaskParameters,
    "link": AccountTaskParameters,
    "unlink": AccountTaskParameters,
    "set_default": AccountTaskParameters,
    "overall_balance": AccountTaskParameters,
    "list_beneficiaries": BeneficiaryTaskParameters,
    "add_beneficiary": BeneficiaryTaskParameters,
    "delete_beneficiary": BeneficiaryTaskParameters,
    "update_beneficiary": BeneficiaryTaskParameters,
    "save_beneficiary": BeneficiaryTaskParameters,
    "transaction_search": QueryTaskParameters,
    "transaction_list": QueryTaskParameters,
    "beneficiary_summary": QueryTaskParameters,
    "handle_request": SupportTaskParameters,
    "report_issue": SupportTaskParameters,
    "answer_question": SupportTaskParameters,
    "resume_session": EmptyTaskParameters,
    "dismiss_resume_session": EmptyTaskParameters,
}

_EXECUTOR_DEFAULT_ACTIONS: dict[str, str] = {
    "transfer": "send_money",
    "airtime": "buy_airtime",
    "data": "buy_data",
    "schedule": "list_scheduled_transactions",
    "account": "check_balance",
    "beneficiary": "list_beneficiaries",
    "query": "transaction_search",
    "support": "handle_request",
    "faq": "answer_question",
    "orchestrator": "resume_session",
}


def _normalized_action(action: str | None) -> str:
    return str(action or "").strip().lower()


def _literal_value(model: PlannerTaskModel, field_name: str) -> str:
    class_value = getattr(model, field_name, None)
    if isinstance(class_value, str):
        return class_value
    args = get_args(model.model_fields[field_name].annotation)
    if len(args) != 1 or not isinstance(args[0], str):
        raise ValueError(f"Planner task model {model.__name__} has invalid {field_name} literal")
    return args[0]


def task_model_for_action(action: str | None) -> PlannerTaskModel:
    """Return the concrete planner task model for a canonical action."""
    normalized = _normalized_action(action)
    if normalized not in _ACTION_TASK_MODELS:
        raise ValueError(f"Unsupported planner task action: {action!r}")
    return _ACTION_TASK_MODELS[normalized]


def task_parameter_model_for_action(action: str | None) -> type[BaseTaskParameters]:
    """Return the canonical parameter model for a planner action."""
    normalized = _normalized_action(action)
    if not normalized:
        return EmptyTaskParameters
    if normalized not in _ACTION_PARAMETER_MODELS:
        raise ValueError(f"Unsupported planner task action: {action!r}")
    return _ACTION_PARAMETER_MODELS[normalized]


def task_parameter_model_for_executor(executor: str | None, action: str | None = None) -> type[BaseTaskParameters]:
    """Return the canonical parameter model for a planner task executor/action pair."""
    normalized_executor = str(executor or "").strip().lower()
    normalized_action = _normalized_action(action)
    if normalized_action:
        return task_parameter_model_for_action(normalized_action)
    default_action = _EXECUTOR_DEFAULT_ACTIONS.get(normalized_executor)
    return task_parameter_model_for_action(default_action)


def coerce_task_parameters(
    executor: str | None,
    action: str | None,
    value: Any,
) -> PlannerTaskParameters:
    """Coerce arbitrary planner parameter input into the executor-specific contract."""
    model = task_parameter_model_for_executor(executor, action)
    if value is None:
        return cast(PlannerTaskParameters, model())
    if isinstance(value, model):
        return cast(PlannerTaskParameters, value)
    if isinstance(value, BaseTaskParameters):
        return cast(PlannerTaskParameters, model.model_validate(value.model_dump(exclude_none=True)))
    return cast(PlannerTaskParameters, model.model_validate(value))


def dump_task_parameters(parameters: BaseTaskParameters | None) -> dict[str, Any]:
    """Dump task parameters into the runtime payload shape."""
    if parameters is None:
        return {}
    return parameters.model_dump(exclude_none=True)


def copy_task_parameters(
    parameters: BaseTaskParameters | None,
    *,
    executor: str | None = None,
    action: str | None = None,
) -> BaseTaskParameters:
    """Deep-copy typed task parameters, creating the executor default when absent."""
    if parameters is None:
        return coerce_task_parameters(executor, action, None)
    return parameters.model_copy(deep=True)


def coerce_planner_task(value: Any) -> PlannerTask:
    """Coerce raw planner task input into its concrete action-specific model."""
    if isinstance(value, BasePlannedTask):
        return cast(PlannerTask, value)
    if not isinstance(value, dict):
        raise TypeError(f"Planner task must be a mapping, got {type(value).__name__}")
    model = task_model_for_action(cast(str | None, value.get("action")))
    return cast(PlannerTask, model.model_validate(value))


def make_planned_task(
    *,
    task_id: str,
    action: str,
    executor: str | None = None,
    instruction: str,
    parameters: Any = None,
    description: str | None = None,
    depends_on: list[str] | None = None,
    condition: str | None = None,
    risk: TaskRisk = "READ_ONLY",
    idempotency_key: str | None = None,
    source_clause_index: int | None = None,
) -> PlannerTask:
    """Create a concrete planner task while enforcing action/executor compatibility."""
    model = task_model_for_action(action)
    expected_action = _normalized_action(action)
    expected_executor = _literal_value(model, "executor")
    normalized_executor = str(executor or expected_executor).strip().lower()
    if normalized_executor != expected_executor:
        raise ValueError(
            f"Planner action {expected_action!r} requires executor {expected_executor!r}, got {executor!r}"
        )
    coerced_parameters = coerce_task_parameters(expected_executor, expected_action, parameters)
    return cast(
        PlannerTask,
        model.model_validate(
            {
                "task_id": task_id,
                "action": expected_action,
                "instruction": instruction,
                "description": description,
                "parameters": coerced_parameters,
                "depends_on": list(depends_on or []),
                "condition": condition,
                "risk": risk,
                "idempotency_key": idempotency_key,
                "source_clause_index": source_clause_index,
            }
        ),
    )


PlannerClauseIntentFamily: TypeAlias = Literal[
    "transfer",
    "airtime",
    "data",
    "account_query",
    "query",
    "support",
    "faq",
    "beneficiary",
    "conversational",
    "unknown",
]


class PlannerClause(BaseModel):
    """Ordered clause decomposition for a single user turn."""

    clause_index: int = Field(..., ge=1, description="1-based ordered semantic clause index")
    text: str = Field(default="", description="User clause text for this semantic segment")
    intent_family: PlannerClauseIntentFamily = Field(
        default="unknown",
        description="Normalized semantic family for this clause",
    )
    extracted_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Clause-local extracted fields used for downstream validation and repair",
    )
    task_ids: list[str] = Field(
        default_factory=list,
        description="Task ids that this clause is expected to map to",
    )


PlannerResponseKey: TypeAlias = Literal[
    "conversational.greeting",
    "conversational.appreciation",
    "conversational.checkin",
    "conversational.identity",
    "conversational.brand_origin",
    "conversational.capability_question",
    "conversational.casual_chat",
    "conversational.out_of_scope",
    "conversational.clarify",
    "planner.cancelled",
]

TransactionExecutor: TypeAlias = Literal["transfer", "airtime", "data"]
BeneficiaryRouteHint: TypeAlias = Literal["beneficiary_list", "recipient_ranking", "none"]
RouterDomainIntent: TypeAlias = Literal[
    "query",
    "account",
    "support",
    "beneficiary",
    "transfer",
    "airtime",
    "data",
    "schedule",
]
AccountActionHint: TypeAlias = Literal[
    "list",
    "list_accounts",
    "count",
    "check_balance",
    "balance",
    "show_balance",
    "overall_balance",
    "link",
    "unlink",
    "set_default",
    "unknown",
    "none",
]

SemanticRoutingDecision: TypeAlias = Literal[
    "direct_reply",
    "direct_context_answer",
    "domain_query",
    "domain_account",
    "domain_support",
    "domain_beneficiary",
    "domain_transfer",
    "domain_airtime",
    "domain_data",
    "domain_schedule",
    "planner_mixed",
    "planner_ambiguous",
    "cancel",
]

SemanticRoutingMode: TypeAlias = Literal["new", "continuation", "quoted_replay", "active_flow_interrupt"]

ContextReadSubtype: TypeAlias = Literal[
    "account_count",
    "linked_accounts_summary",
    "default_account_identity",
    "pending_mandate_explanation",
    "account_mandate_readiness_summary",
    "account_linked_bank_existence_check",
    "beneficiary_count",
    "beneficiary_list",
    "beneficiary_existence_check",
    "beneficiary_name_match_preview",
    "flow_recap",
    "flow_missing_requirements",
]


InterruptRoutingDecision: TypeAlias = Literal[
    "continue_flow",
    "switch_intent",
    "cancel",
    "unclear",
    "approve_flow",
    "reject_flow",
    "status_query",
    "active_flow_question",
]

ActiveFlowQuestionType: TypeAlias = Literal[
    "recap",
    "requirements",
    "why_required",
    "confirmation_effect",
    "cancellation_effect",
    "auth_pin_reason",
    "source_account",
    "editable_fields",
    "current_value",
    "timing_or_status",
    "fees_or_charges",
    "unsupported_or_unsafe",
    "unknown",
]

ContextFrameFollowupAction: TypeAlias = Literal[
    "answer_completeness",
    "lookup_entity",
    "show_details",
    "filter_items",
    "compare_items",
    "select_item",
    "explain_result",
    "replay_tasks",
    "edit_schedule",
    "cancel_schedule",
    "start_new_task",
    "completeness_check",
    "entity_lookup",
    "detail_request",
    "selection",
    "replay",
    "new_task",
    "unclear",
]

ContextFrameRequestedField: TypeAlias = Literal[
    "amount",
    "bank",
    "counterparty",
    "date",
    "network",
    "phone",
    "reference",
    "status",
]

ContextFrameRank: TypeAlias = Literal["largest", "smallest", "newest", "oldest"]


class ContextFrameFollowupFilters(BaseModel):
    """Structured filters for grounding follow-ups against displayed result frames."""

    model_config = ConfigDict(extra="forbid")

    transaction_type: str | None = Field(
        default=None,
        description="Visible transaction type or task type filter, for example transfer, airtime, data, credit, debit",
    )
    status: str | None = Field(default=None, description="Visible status filter")
    direction: str | None = Field(default=None, description="Visible transaction direction filter")
    bank: str | None = Field(default=None, description="Visible bank name/reference filter")
    counterparty: str | None = Field(default=None, description="Visible counterparty/recipient/merchant filter")


PendingActionEditOperation: TypeAlias = Literal[
    "remove_tasks",
    "restore_tasks",
    "update_fields",
    "add_tasks",
    "approve_flow",
    "cancel_all",
    "status_query",
    "switch_intent",
    "show_options",
    "unclear",
]


class PendingActionFieldUpdates(BaseModel):
    """Allowed pending confirmation field updates from semantic classification.

    This model is intentionally closed for OpenAI structured-output schemas.
    The LLM may classify requested edits into these slots; deterministic code still
    validates whether each slot is applicable to the targeted task(s).
    """

    model_config = ConfigDict(extra="forbid")

    amount: MoneyAmount | None = Field(default=None, description="Updated transaction amount")
    narration: str | None = Field(default=None, description="Updated transfer narration")
    recipient_name: str | None = Field(default=None, description="Updated recipient/beneficiary reference")
    recipient_account: str | None = Field(default=None, description="Updated recipient account number")
    recipient_bank_name: str | None = Field(default=None, description="Updated recipient bank name")
    source_bank_name: str | None = Field(default=None, description="Updated source account bank reference")
    source_account_index: int | None = Field(default=None, description="1-based source account selection index")
    use_dual_accounts: bool | None = Field(default=None, description="Whether to pool funding across accounts")
    source_accounts: list[str] | None = Field(default=None, description="Source accounts/banks requested for pooling")
    funding_splits: list[FundingSplitUpdate] | None = Field(
        default=None,
        description="Explicit source-account funding split for a pending transfer",
    )
    phone: str | None = Field(default=None, description="Updated airtime/data phone number")
    network: str | None = Field(default=None, description="Updated airtime/data network")
    size_preference: str | None = Field(default=None, description="Updated data-plan size preference")
    validity_preference: str | None = Field(default=None, description="Updated data-plan validity preference")
    selection_preference: str | None = Field(default=None, description="Updated data-plan selection preference")
    usage_intent: str | None = Field(default=None, description="Updated data-plan usage intent")
    show_options: bool | None = Field(default=None, description="Whether to show alternate data-plan options")


class PendingActionTargetedUpdate(BaseModel):
    """One scoped edit against pending confirmation task(s)."""

    model_config = ConfigDict(extra="forbid")

    target_task_ids: list[str] = Field(
        default_factory=list,
        description="Task ids explicitly inferred from context for this scoped edit",
    )
    target_types: list[Literal["transfer", "airtime", "data"]] = Field(
        default_factory=list,
        description="Transaction task types targeted by this scoped edit",
    )
    target_texts: list[str] = Field(
        default_factory=list,
        description="Natural-language target references for this scoped edit",
    )
    fields: PendingActionFieldUpdates = Field(
        default_factory=PendingActionFieldUpdates,
        description="Field updates to apply to the resolved target task(s)",
    )


class PendingActionEditDecision(BaseModel):
    """LLM interpretation of a user turn relative to pending confirmation tasks.

    The model only classifies the semantic edit request. Deterministic code must
    still resolve task ids, validate ambiguity, and apply any state mutation.
    """

    model_config = ConfigDict(extra="forbid")

    operation: PendingActionEditOperation = Field(
        default="unclear",
        description="Semantic operation requested against the pending confirmation batch",
    )
    confidence: float = Field(default=0.0, description="Confidence in the pending-action edit interpretation")
    detected_language: str | None = Field(default=None, description="Detected language for the user turn")
    target_task_ids: list[str] = Field(
        default_factory=list,
        description="Task ids explicitly inferred from the pending task context; suggestions only",
    )
    target_types: list[Literal["transfer", "airtime", "data"]] = Field(
        default_factory=list,
        description="Transaction task types targeted by the edit",
    )
    target_texts: list[str] = Field(
        default_factory=list,
        description="Natural-language target references such as recipient, amount, bank, phone, or 'both transfers'",
    )
    updates: list[PendingActionTargetedUpdate] = Field(
        default_factory=list,
        description="Scoped field updates when one message edits multiple targets differently",
    )
    amount: MoneyAmount | None = Field(default=None, description="Updated transaction amount")
    narration: str | None = Field(default=None, description="Updated transfer narration")
    recipient_name: str | None = Field(default=None, description="Updated recipient/beneficiary reference")
    recipient_account: str | None = Field(default=None, description="Updated recipient account number")
    recipient_bank_name: str | None = Field(default=None, description="Updated recipient bank name")
    source_bank_name: str | None = Field(default=None, description="Updated source account bank reference")
    source_account_index: int | None = Field(default=None, description="1-based source account selection index")
    use_dual_accounts: bool | None = Field(default=None, description="Whether to pool funding across accounts")
    source_accounts: list[str] | None = Field(default=None, description="Source accounts/banks requested for pooling")
    funding_splits: list[FundingSplitUpdate] | None = Field(
        default=None,
        description="Explicit source-account funding split for a pending transfer",
    )
    phone: str | None = Field(default=None, description="Updated airtime/data phone number")
    network: str | None = Field(default=None, description="Updated airtime/data network")
    size_preference: str | None = Field(default=None, description="Updated data-plan size preference")
    validity_preference: str | None = Field(default=None, description="Updated data-plan validity preference")
    selection_preference: str | None = Field(default=None, description="Updated data-plan selection preference")
    usage_intent: str | None = Field(default=None, description="Updated data-plan usage intent")
    show_options: bool | None = Field(default=None, description="Whether to show alternate data-plan options")
    add_instruction: str | None = Field(
        default=None,
        description="Fresh user instruction to route when operation=add_tasks",
    )
    status_query_type: Literal["recap", "requirements"] | None = Field(
        default=None,
        description="Subtype when operation=status_query",
    )
    target_intent: str | None = Field(
        default=None,
        description="Intent to switch to when operation=switch_intent",
    )
    reason: str | None = Field(default=None, description="Short explanation for observability/debugging")

    @property
    def fields(self) -> PendingActionFieldUpdates:
        return PendingActionFieldUpdates(
            amount=self.amount,
            narration=self.narration,
            recipient_name=self.recipient_name,
            recipient_account=self.recipient_account,
            recipient_bank_name=self.recipient_bank_name,
            source_bank_name=self.source_bank_name,
            source_account_index=self.source_account_index,
            use_dual_accounts=self.use_dual_accounts,
            source_accounts=self.source_accounts,
            funding_splits=self.funding_splits,
            phone=self.phone,
            network=self.network,
            size_preference=self.size_preference,
            validity_preference=self.validity_preference,
            selection_preference=self.selection_preference,
            usage_intent=self.usage_intent,
            show_options=self.show_options,
        )


class ContextFrameFollowupDecision(BaseModel):
    """LLM interpretation of a user turn relative to the latest displayed response frame."""

    model_config = ConfigDict(extra="forbid")

    decision: ContextFrameFollowupAction = Field(
        default="unclear",
        description="Semantic action relative to the latest displayed frame",
    )
    confidence: float = Field(default=0.0, description="Confidence in the frame-follow-up interpretation")
    detected_language: str | None = Field(default=None, description="Detected language for the user turn")
    target_text: str | None = Field(
        default=None,
        description="User's referenced displayed entity, label, bank, recipient, group, or other visible target",
    )
    requested_field: ContextFrameRequestedField | None = Field(
        default=None,
        description="Specific safe displayed field the user asks about",
    )
    rank: ContextFrameRank | None = Field(
        default=None,
        description="Ranking selector when the user asks for largest/smallest/newest/oldest displayed item",
    )
    filters: ContextFrameFollowupFilters | None = Field(
        default=None,
        description="Structured filters to narrow displayed items",
    )
    selection_index: int | None = Field(
        default=None,
        description="1-based selected item index when the user chooses an item by number or ordinal",
    )
    reason: str | None = Field(default=None, description="Short explanation for observability/debugging")


class ContextFrameReplayModifier(BaseModel):
    """Strict modifier extraction for replaying displayed transaction items.

    The displayed transaction remains the authoritative base. These fields are only
    optional edits explicitly present in the user's latest replay message.
    """

    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(default=0.0, description="Confidence in the replay modifier extraction")
    detected_language: str | None = Field(default=None, description="Detected language for the user turn")
    amount: MoneyAmount | None = Field(default=None, gt=0, description="Replacement transaction amount, if explicit")
    amount_evidence: str | None = Field(
        default=None,
        description="Exact user-message phrase supporting amount, else null",
    )
    source_account_reference: str | None = Field(
        default=None,
        description="User's explicit source account/bank reference, if any",
    )
    source_account_evidence: str | None = Field(
        default=None,
        description="Exact user-message phrase supporting source_account_reference, else null",
    )
    narration: str | None = Field(default=None, description="Replacement transfer narration/memo/note, if explicit")
    narration_evidence: str | None = Field(
        default=None,
        description="Exact user-message phrase supporting narration, else null",
    )
    reason: str | None = Field(default=None, description="Short explanation for observability/debugging")


class BatchSlotPatchUpdate(BaseModel):
    """One scoped slot update for an active pre-auth transaction batch."""

    model_config = ConfigDict(extra="forbid")

    target_task_id: str | None = Field(default=None, description="Known task id this update targets, if inferred")
    target_texts: list[str] = Field(
        default_factory=list,
        description="Natural-language references such as recipient alias, amount, or 'both transfers'",
    )
    recipient_account: str | None = Field(default=None, description="Destination account number")
    recipient_bank_name: str | None = Field(default=None, description="Destination bank name")
    amount: MoneyAmount | None = Field(default=None, description="Updated transfer amount")
    narration: str | None = Field(default=None, description="Updated transfer narration/memo")
    source_bank_name: str | None = Field(default=None, description="Source account bank reference")
    source_accounts: list[str] | None = Field(default=None, description="Source accounts/banks requested for pooling")
    use_dual_accounts: bool | None = Field(default=None, description="Whether pooled funding is requested")


class BatchSlotPatchDecision(BaseModel):
    """LLM interpretation of a slot-filling turn for an active transaction batch."""

    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(default=0.0, description="Confidence in the slot patch interpretation")
    detected_language: str | None = Field(default=None, description="Detected user language")
    updates: list[BatchSlotPatchUpdate] = Field(
        default_factory=list,
        description="Scoped updates to validate and apply to active batch tasks",
    )
    needs_clarification: bool = Field(default=False, description="True when the user reply is ambiguous")
    clarification: str | None = Field(default=None, description="Short clarification prompt if needed")
    reason: str | None = Field(default=None, description="Short observability/debugging reason")


class InterruptRouteDecision(BaseModel):
    """LLM decision for pending-input routing while a session is active."""

    decision: InterruptRoutingDecision = Field(
        description="Routing decision for pending-input turn",
    )
    confidence: float = Field(default=0.0, description="Confidence in routing decision (0.0-1.0)")
    detected_language: str | None = Field(
        default=None,
        description="Detected language for the turn",
    )
    target_intent: str | None = Field(
        default=None,
        description="Intent to switch to when decision=switch_intent",
    )
    target_mode: Literal["new", "continuation"] | None = Field(
        default=None,
        description="Optional routing mode hint (for example query new-vs-continuation)",
    )
    status_query_type: Literal["recap", "requirements"] | None = Field(
        default=None,
        description="Subtype when decision=status_query",
    )
    question_type: ActiveFlowQuestionType | None = Field(
        default=None,
        description="Subtype when decision=active_flow_question",
    )
    target_field: str | None = Field(
        default=None,
        description="Active-flow field the question refers to, if known",
    )
    unsafe_reason: str | None = Field(
        default=None,
        description="Safety/unsupported reason when question_type=unsupported_or_unsafe",
    )
    reason: str | None = Field(default=None, description="Short explanation for observability/debugging")


class SemanticRouteDecision(BaseModel):
    """LLM decision for first-pass semantic routing before planner-owned dispatch."""

    decision: SemanticRoutingDecision = Field(default="planner_ambiguous", description="Top-level routing action")
    confidence: float = Field(default=0.0, description="Confidence in routing decision (0.0-1.0)")
    detected_language: str | None = Field(default=None, description="Detected language for this turn")
    requested_language: str | None = Field(
        default=None,
        description="Explicit language requested for switch when user asks to change locale.",
    )
    mode: SemanticRoutingMode | None = Field(
        default=None,
        description="Optional routing mode hint such as new-vs-continuation semantics.",
    )
    target_intent: RouterDomainIntent | None = Field(
        default=None,
        description="Optional normalized domain owner for observability/debugging.",
    )
    response_key: PlannerResponseKey | None = Field(
        default=None,
        description="Deterministic keyed response when decision=direct_reply or cancel",
    )
    response: str | None = Field(
        default=None,
        description="Direct response text when decision=direct_reply or direct_context_answer",
    )
    expected_transaction_executors: list[TransactionExecutor] = Field(
        default_factory=list,
        description="Explicit transaction executors expected from planner, when known",
    )
    schedule_response_mode: Literal["list", "count"] | None = Field(
        default=None,
        description="For simple scheduled-transaction read intents, whether the user wants a list or count.",
    )
    reason: str | None = Field(default=None, description="Short explanation for observability/debugging")


def _strip_llm_schema_annotations(schema: dict[str, Any]) -> None:
    """Remove non-validation annotations from the planner LLM schema."""

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


class PlannerOutput(BaseModel):
    """Structured output returned by the planner LLM.

    Combines classification and planning into single model.
    """

    model_config = ConfigDict(json_schema_extra=_strip_llm_schema_annotations)

    # Classification fields
    primary_intent: str = Field(
        description=(
            "Primary intent: transfer, airtime, data, query, account, support, faq, conversational, cancel, mixed"
        )
    )
    response: str = Field(default="", description="Transitional acknowledgment text for non-keyed cases")
    response_key: PlannerResponseKey | None = Field(
        default=None,
        description="Deterministic keyed response for conversational/cancel paths",
    )
    confidence: float = Field(default=0.9, description="Confidence in classification (0.0-1.0)")
    is_complex: bool = Field(
        default=False, description="True if multiple recipients, mixed intents, or complex request"
    )
    is_cancellation: bool = Field(default=False, description="True if user wants to cancel/abort")
    is_confirmation: bool = Field(default=False, description="True if user explicitly confirms/agrees")
    detected_language: str | None = Field(
        default=None, description="Detected language: English, Yoruba, Hausa, Igbo, Pidgin, French"
    )
    context_read_subtype: ContextReadSubtype | None = Field(
        default=None,
        description=(
            "Set only for context-backed read-only account/beneficiary asks that are eligible for planner-owned "
            "context-read synthesis; otherwise null"
        ),
    )
    beneficiary_route: BeneficiaryRouteHint = Field(
        default="none",
        description=(
            "Beneficiary routing hint from planner: "
            "beneficiary_list when asking to view/manage saved beneficiaries, "
            "recipient_ranking when asking who user sends to most, otherwise none"
        ),
    )
    account_action_hint: AccountActionHint = Field(
        default="none",
        description=(
            "Account action hint for planner context-read disambiguation: "
            "list/list_accounts/count/check_balance/link/unlink/set_default, else none"
        ),
    )

    # Planning fields
    normalized_instruction: str = Field(default="", description="Cleaned up version of user request")
    clauses: list[PlannerClause] = Field(
        default_factory=list,
        description="Ordered semantic clause decomposition for the turn",
    )
    tasks: list[PlannedTask] = Field(default_factory=list)
    notes: str | None = None
    created_at: float = Field(default_factory=lambda: __import__("time").time())

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        _strip_llm_schema_annotations(schema)
        schema["title"] = cls.__name__
        return schema


class PlannerOutputTransferOnly(PlannerOutput):
    """LLM-facing planner output for transfer-only turns."""

    tasks: list[TransferActionPlannerTask] = Field(default_factory=list)  # type: ignore[assignment]


class PlannerOutputAirtimeOnly(PlannerOutput):
    """LLM-facing planner output for airtime-only turns."""

    tasks: list[AirtimeActionPlannerTask] = Field(default_factory=list)  # type: ignore[assignment]


class PlannerOutputDataOnly(PlannerOutput):
    """LLM-facing planner output for data-only turns."""

    tasks: list[DataActionPlannerTask] = Field(default_factory=list)  # type: ignore[assignment]


class PlannerOutputTransferAirtime(PlannerOutput):
    """LLM-facing planner output for transfer+airtime turns."""

    tasks: list[TransferAirtimeActionPlannerTask] = Field(default_factory=list)  # type: ignore[assignment]


class PlannerOutputTransferData(PlannerOutput):
    """LLM-facing planner output for transfer+data turns."""

    tasks: list[TransferDataActionPlannerTask] = Field(default_factory=list)  # type: ignore[assignment]


class PlannerOutputAirtimeData(PlannerOutput):
    """LLM-facing planner output for airtime+data turns."""

    tasks: list[AirtimeDataActionPlannerTask] = Field(default_factory=list)  # type: ignore[assignment]


class PlannerOutputTransactionsOnly(PlannerOutput):
    """LLM-facing planner output for transaction-only turns when executor scope is broad."""

    tasks: list[TransactionalActionPlannerTask] = Field(default_factory=list)  # type: ignore[assignment]


_TRANSACTION_OUTPUT_MODELS_BY_EXECUTORS: dict[frozenset[str], type[PlannerOutput]] = {
    frozenset({"transfer"}): PlannerOutputTransferOnly,
    frozenset({"airtime"}): PlannerOutputAirtimeOnly,
    frozenset({"data"}): PlannerOutputDataOnly,
    frozenset({"transfer", "airtime"}): PlannerOutputTransferAirtime,
    frozenset({"transfer", "data"}): PlannerOutputTransferData,
    frozenset({"airtime", "data"}): PlannerOutputAirtimeData,
    frozenset({"transfer", "airtime", "data"}): PlannerOutputTransactionsOnly,
}


def planner_output_model_for_transaction_executors(executors: Iterable[str]) -> type[PlannerOutput]:
    """Return the narrow LLM-facing output model for a transaction executor scope."""
    normalized = frozenset(
        executor
        for executor in (str(item).strip().lower() for item in executors)
        if executor in {"transfer", "airtime", "data"}
    )
    if not normalized:
        return PlannerOutputTransactionsOnly
    return _TRANSACTION_OUTPUT_MODELS_BY_EXECUTORS.get(normalized, PlannerOutputTransactionsOnly)
