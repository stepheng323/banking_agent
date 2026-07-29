"""Models for task planning and normalization."""

from collections.abc import Iterable
from typing import Annotated, Any, ClassVar, Literal, TypeAlias, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.money import MoneyAmount
from shared.types.amount_mutation import AmountMutation, set_amount_mutation
from shared.types.balance import BalanceFollowupDelta, BalanceQueryContract, initial_balance_contract
from shared.types.conversation_sets import (
    AccountLifecycleContract,
    AccountLifecycleFollowupDelta,
    BeneficiaryFollowupDelta,
    BeneficiaryQueryContract,
    EntitySelectionRef,
    ScheduleQueryContract,
    SetAmountAllocation,
    SetScopeDelta,
)
from shared.types.query_preferences import QueryPreferenceUpdate
from shared.types.read import ReadRequest, ReadSubject, ResponseShape

QueryInsightType: TypeAlias = Literal[
    "variance_drivers",
    "probable_duplicates",
    "recurring_patterns",
    "anomalies",
    "counterparty_concentration",
    "forecast",
    "runway",
    "cash_flow_quality",
]


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

    read_request: ReadRequest | None = None
    balance_contract: BalanceQueryContract | None = None
    account_lifecycle_contract: AccountLifecycleContract | None = None
    bank_name: str | None = None
    source_bank_name: str | None = None
    source_account_index: int | None = None
    alias: str | None = None

    @model_validator(mode="after")
    def validate_balance_contract(self) -> "AccountTaskParameters":
        if self.balance_contract is not None and (
            self.read_request is None or self.read_request.subject != "balance"
        ):
            raise ValueError("balance_contract requires a balance read_request")
        if self.account_lifecycle_contract is not None and (
            self.read_request is None or self.read_request.subject not in {"linked_account", "default_account"}
        ):
            raise ValueError("account_lifecycle_contract requires a linked-account read_request")
        if self.read_request is not None and self.read_request.subject == "balance" and self.balance_contract is None:
            raise ValueError("balance reads require balance_contract")
        if (
            self.read_request is not None
            and self.read_request.subject in {"linked_account", "default_account"}
            and self.account_lifecycle_contract is None
        ):
            raise ValueError("linked-account reads require account_lifecycle_contract")
        return self


class BeneficiaryTaskParameters(BaseTaskParameters):
    """Beneficiary task parameters."""

    model_config = ConfigDict(extra="forbid")

    read_request: ReadRequest | None = None
    beneficiary_contract: BeneficiaryQueryContract | None = None
    name_filter: str | None = None
    recipient: str | None = None
    recipient_name: str | None = None
    recipient_account: str | None = None
    bank_name: str | None = None
    phone: str | None = None
    target_phone: str | None = None
    alias: str | None = None
    beneficiary_id: str | None = None
    beneficiary_selection_ref: EntitySelectionRef | None = None
    new_alias: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_beneficiary_contract(self) -> "BeneficiaryTaskParameters":
        if self.beneficiary_contract is not None and (
            self.read_request is None or self.read_request.subject != "beneficiary"
        ):
            raise ValueError("beneficiary_contract requires a beneficiary read_request")
        if self.read_request is not None and self.beneficiary_contract is None:
            raise ValueError("beneficiary reads require beneficiary_contract")
        return self


class ScheduleTaskParameters(BaseTaskParameters):
    """Scheduled-transaction management parameters."""

    model_config = ConfigDict(extra="forbid")

    read_request: ReadRequest | None = None
    schedule_contract: ScheduleQueryContract | None = None
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
    source_bank_name: str | None = None
    source_account_index: int | None = None

    @model_validator(mode="after")
    def validate_schedule_contract(self) -> "ScheduleTaskParameters":
        if self.schedule_contract is not None and (
            self.read_request is None or self.read_request.subject != "schedule"
        ):
            raise ValueError("schedule_contract requires a schedule read_request")
        if self.read_request is not None and self.schedule_contract is None:
            raise ValueError("schedule reads require schedule_contract")
        return self


class QueryTaskParameters(BaseTaskParameters):
    """Read-only query task parameters."""

    model_config = ConfigDict(extra="forbid")

    read_request: ReadRequest | None = None


class QueryPreferenceTaskParameters(BaseTaskParameters):
    """Explicit query-preference mutation parameters."""

    model_config = ConfigDict(extra="forbid")

    preferences_update: QueryPreferenceUpdate


class SupportTaskParameters(BaseTaskParameters):
    """Support/FAQ task parameters."""

    model_config = ConfigDict(extra="forbid")

    read_request: ReadRequest | None = None
    ticket_code: str | None = Field(default=None, max_length=32)
    ticket_id: str | None = Field(default=None, max_length=160)
    ticket_note: str | None = Field(default=None, min_length=1, max_length=1000)
    offset: int = Field(default=0, ge=0)


PlannerTaskParameters: TypeAlias = (
    TransferTaskParameters
    | AirtimeTaskParameters
    | DataTaskParameters
    | AccountTaskParameters
    | BeneficiaryTaskParameters
    | ScheduleTaskParameters
    | QueryTaskParameters
    | QueryPreferenceTaskParameters
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

    @model_validator(mode="after")
    def reject_read_contract_on_mutation(self) -> "BasePlannedTask":
        parameters = getattr(self, "parameters", None)
        if self.risk != "READ_ONLY" and getattr(parameters, "read_request", None) is not None:
            raise ValueError("read_request is valid only for READ_ONLY tasks")
        if self.risk != "READ_ONLY" and getattr(parameters, "balance_contract", None) is not None:
            raise ValueError("balance_contract is valid only for READ_ONLY tasks")
        return self


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


class DataPlanQueryTask(BasePlannedTask):
    action: Literal["data_plan_query"]
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


class PauseScheduledTransactionTask(BasePlannedTask):
    action: Literal["pause_scheduled_transaction"]
    executor: ClassVar[Literal["schedule"]] = "schedule"
    parameters: ScheduleTaskParameters = Field(default_factory=ScheduleTaskParameters)


class ResumeScheduledTransactionTask(BasePlannedTask):
    action: Literal["resume_scheduled_transaction"]
    executor: ClassVar[Literal["schedule"]] = "schedule"
    parameters: ScheduleTaskParameters = Field(default_factory=ScheduleTaskParameters)


class ListScheduledRunsTask(BasePlannedTask):
    action: Literal["list_scheduled_runs"]
    executor: ClassVar[Literal["schedule"]] = "schedule"
    parameters: ScheduleTaskParameters = Field(default_factory=ScheduleTaskParameters)


class FindScheduledRunTask(BasePlannedTask):
    action: Literal["find_scheduled_run"]
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


class ListBeneficiariesTask(BasePlannedTask):
    action: Literal["list_beneficiaries"]
    executor: ClassVar[Literal["beneficiary"]] = "beneficiary"
    parameters: BeneficiaryTaskParameters = Field(default_factory=BeneficiaryTaskParameters)


class DeleteBeneficiaryTask(BasePlannedTask):
    action: Literal["delete_beneficiary"]
    executor: ClassVar[Literal["beneficiary"]] = "beneficiary"
    parameters: BeneficiaryTaskParameters = Field(default_factory=BeneficiaryTaskParameters)


class RenameBeneficiaryTask(BasePlannedTask):
    action: Literal["rename_beneficiary"]
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


class ListSupportTicketsTask(BasePlannedTask):
    action: Literal["list_support_tickets"]
    executor: ClassVar[Literal["support"]] = "support"
    parameters: SupportTaskParameters = Field(default_factory=SupportTaskParameters)


class FindSupportTicketTask(BasePlannedTask):
    action: Literal["find_support_ticket"]
    executor: ClassVar[Literal["support"]] = "support"
    parameters: SupportTaskParameters = Field(default_factory=SupportTaskParameters)


class AppendSupportTicketNoteTask(BasePlannedTask):
    action: Literal["append_support_ticket_note"]
    executor: ClassVar[Literal["support"]] = "support"
    parameters: SupportTaskParameters = Field(default_factory=SupportTaskParameters)


class CloseSupportTicketTask(BasePlannedTask):
    action: Literal["close_support_ticket"]
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
    action: Literal["buy_data", "data_plan_query", "schedule_data", "recurring_data"]
    executor: ClassVar[Literal["data"]] = "data"
    parameters: DataTaskParameters = Field(default_factory=DataTaskParameters)


class SchedulePlannedTask(BasePlannedTask):
    action: Literal[
        "list_scheduled_transactions",
        "find_scheduled_transaction",
        "cancel_scheduled_transaction",
        "edit_scheduled_transaction",
        "pause_scheduled_transaction",
        "resume_scheduled_transaction",
        "list_scheduled_runs",
        "find_scheduled_run",
    ]
    executor: ClassVar[Literal["schedule"]] = "schedule"
    parameters: ScheduleTaskParameters = Field(default_factory=ScheduleTaskParameters)


class AccountPlannedTask(BasePlannedTask):
    action: Literal[
        "check_balance",
        "list_accounts",
        "count",
        "get_default",
        "link",
        "unlink",
        "set_default",
        "reinitiate_mandate",
    ]
    executor: ClassVar[Literal["account"]] = "account"
    parameters: AccountTaskParameters = Field(default_factory=AccountTaskParameters)


class BeneficiaryPlannedTask(BasePlannedTask):
    action: Literal[
        "list_beneficiaries",
        "delete_beneficiary",
        "rename_beneficiary",
        "save_beneficiary",
    ]
    executor: ClassVar[Literal["beneficiary"]] = "beneficiary"
    parameters: BeneficiaryTaskParameters = Field(default_factory=BeneficiaryTaskParameters)


class QueryPlannedTask(BasePlannedTask):
    action: Literal["transaction_search", "transaction_list", "beneficiary_summary", "update_query_preferences"]
    executor: ClassVar[Literal["query"]] = "query"
    parameters: QueryTaskParameters | QueryPreferenceTaskParameters = Field(default_factory=QueryTaskParameters)

    @model_validator(mode="after")
    def validate_query_action_parameters(self) -> "QueryPlannedTask":
        is_preference_update = self.action == "update_query_preferences"
        if is_preference_update != isinstance(self.parameters, QueryPreferenceTaskParameters):
            raise ValueError("query action and parameter contract do not match")
        return self


class SupportPlannedTask(BasePlannedTask):
    action: Literal[
        "handle_request",
        "report_issue",
        "list_support_tickets",
        "find_support_ticket",
        "append_support_ticket_note",
        "close_support_ticket",
    ]
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
    "data_plan_query": DataPlannedTask,
    "schedule_data": DataPlannedTask,
    "recurring_data": DataPlannedTask,
    "list_scheduled_transactions": SchedulePlannedTask,
    "find_scheduled_transaction": SchedulePlannedTask,
    "cancel_scheduled_transaction": SchedulePlannedTask,
    "edit_scheduled_transaction": SchedulePlannedTask,
    "pause_scheduled_transaction": SchedulePlannedTask,
    "resume_scheduled_transaction": SchedulePlannedTask,
    "list_scheduled_runs": SchedulePlannedTask,
    "find_scheduled_run": SchedulePlannedTask,
    "check_balance": AccountPlannedTask,
    "list_accounts": AccountPlannedTask,
    "count": AccountPlannedTask,
    "get_default": AccountPlannedTask,
    "link": AccountPlannedTask,
    "unlink": AccountPlannedTask,
    "set_default": AccountPlannedTask,
    "reinitiate_mandate": AccountPlannedTask,
    "list_beneficiaries": BeneficiaryPlannedTask,
    "delete_beneficiary": BeneficiaryPlannedTask,
    "rename_beneficiary": BeneficiaryPlannedTask,
    "save_beneficiary": BeneficiaryPlannedTask,
    "transaction_search": QueryPlannedTask,
    "transaction_list": QueryPlannedTask,
    "beneficiary_summary": QueryPlannedTask,
    "update_query_preferences": QueryPlannedTask,
    "handle_request": SupportPlannedTask,
    "report_issue": SupportPlannedTask,
    "list_support_tickets": SupportPlannedTask,
    "find_support_ticket": SupportPlannedTask,
    "append_support_ticket_note": SupportPlannedTask,
    "close_support_ticket": SupportPlannedTask,
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
    "data_plan_query": DataTaskParameters,
    "schedule_data": DataTaskParameters,
    "recurring_data": DataTaskParameters,
    "list_scheduled_transactions": ScheduleTaskParameters,
    "find_scheduled_transaction": ScheduleTaskParameters,
    "cancel_scheduled_transaction": ScheduleTaskParameters,
    "edit_scheduled_transaction": ScheduleTaskParameters,
    "pause_scheduled_transaction": ScheduleTaskParameters,
    "resume_scheduled_transaction": ScheduleTaskParameters,
    "list_scheduled_runs": ScheduleTaskParameters,
    "find_scheduled_run": ScheduleTaskParameters,
    "check_balance": AccountTaskParameters,
    "list_accounts": AccountTaskParameters,
    "count": AccountTaskParameters,
    "get_default": AccountTaskParameters,
    "link": AccountTaskParameters,
    "unlink": AccountTaskParameters,
    "set_default": AccountTaskParameters,
    "reinitiate_mandate": AccountTaskParameters,
    "list_beneficiaries": BeneficiaryTaskParameters,
    "delete_beneficiary": BeneficiaryTaskParameters,
    "rename_beneficiary": BeneficiaryTaskParameters,
    "save_beneficiary": BeneficiaryTaskParameters,
    "transaction_search": QueryTaskParameters,
    "transaction_list": QueryTaskParameters,
    "beneficiary_summary": QueryTaskParameters,
    "update_query_preferences": QueryPreferenceTaskParameters,
    "handle_request": SupportTaskParameters,
    "report_issue": SupportTaskParameters,
    "list_support_tickets": SupportTaskParameters,
    "find_support_ticket": SupportTaskParameters,
    "append_support_ticket_note": SupportTaskParameters,
    "close_support_ticket": SupportTaskParameters,
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
    "capability.unsupported_unavailable",
]

SemanticRouterResponseKey: TypeAlias = Literal[
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
    "capability.unsupported_unavailable",
    "meta.melkor_easter_egg",
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
    "faq",
]
AccountActionHint: TypeAlias = Literal[
    "list",
    "list_accounts",
    "count",
    "check_balance",
    "balance",
    "show_balance",
    "overall_balance",
    "get_default",
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
    "domain_faq",
    "planner_mixed",
    "planner_ambiguous",
    "cancel",
]

SemanticRoutingMode: TypeAlias = Literal["new", "continuation", "quoted_replay", "active_flow_interrupt"]

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
    "funding_affordability",
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
    "pause_schedule",
    "resume_schedule",
    "delete_beneficiary",
    "rename_beneficiary",
    "append_ticket_note",
    "close_ticket",
    "unlink_account",
    "set_default_account",
    "relink_account",
    "transfer_beneficiaries",
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

    amount: MoneyAmount | None = Field(
        default=None,
        description="Deprecated absolute replacement amount; normalized to amount_mutation internally",
    )
    amount_mutation: AmountMutation | None = Field(
        default=None,
        description="Authoritative bounded mutation of the current pending transaction amount",
    )
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
    target_task_ids: list[str] | None = Field(
        default=None,
        description="Task ids explicitly inferred from the pending task context; suggestions only",
    )
    target_types: list[Literal["transfer", "airtime", "data"]] | None = Field(
        default=None,
        description="Transaction task types targeted by the edit",
    )
    target_texts: list[str] | None = Field(
        default=None,
        description="Natural-language target references such as recipient, amount, bank, phone, or 'both transfers'",
    )
    updates: list[PendingActionTargetedUpdate] | None = Field(
        default=None,
        description="Scoped field updates when one message edits multiple targets differently",
    )
    amount: MoneyAmount | None = Field(
        default=None,
        description="Deprecated absolute replacement amount; normalized to amount_mutation internally",
    )
    amount_mutation: AmountMutation | None = Field(
        default=None,
        description="Authoritative bounded mutation of the current pending transaction amount",
    )
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
    account_action: AccountActionHint | None = Field(
        default=None,
        description="Typed read/action when switch_intent targets the account domain",
    )
    reason: str | None = Field(default=None, description="Short explanation for observability/debugging")

    @property
    def fields(self) -> PendingActionFieldUpdates:
        amount_mutation = self.amount_mutation
        if amount_mutation is None and self.amount is not None:
            amount_mutation = set_amount_mutation(self.amount)
        return PendingActionFieldUpdates(
            amount=self.amount,
            amount_mutation=amount_mutation,
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


class ScheduleSetEditDelta(BaseModel):
    """Sparse, explicit patch for a reviewed set of scheduled instructions."""

    model_config = ConfigDict(extra="forbid")

    amount: MoneyAmount | None = Field(default=None, gt=0)
    schedule_mode: Literal["one_time", "recurring"] | None = None
    recurrence_type: Literal["one_time", "daily", "weekly", "monthly"] | None = None
    schedule_timezone: str | None = Field(default=None, max_length=80)
    schedule_start_date: str | None = Field(default=None, max_length=32)
    schedule_time_local: str | None = Field(default=None, max_length=16)
    schedule_day_of_week: int | None = Field(default=None, ge=0, le=6)
    schedule_day_of_month: int | None = Field(default=None, ge=1, le=31)
    schedule_end_date: str | None = Field(default=None, max_length=32)
    source_account_id: str | None = Field(default=None, max_length=160)
    source_bank_name: str | None = Field(default=None, max_length=120)
    recipient_name: str | None = Field(default=None, max_length=160)
    recipient_account: str | None = Field(default=None, max_length=32)
    recipient_bank_name: str | None = Field(default=None, max_length=120)
    narration: str | None = Field(default=None, max_length=240)
    recipient_phone: str | None = Field(default=None, max_length=24)
    target_phone: str | None = Field(default=None, max_length=24)
    network: str | None = Field(default=None, max_length=40)
    plan_code: str | None = Field(default=None, max_length=120)
    plan_name: str | None = Field(default=None, max_length=160)


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
    read_response_shape: ResponseShape | None = Field(
        default=None,
        description="Requested presentation for a retained canonical read, if applicable",
    )
    read_subject: ReadSubject | None = Field(
        default=None,
        description="Banking read subject intended by this turn; distinguishes retained refinements from pivots",
    )
    page_action: Literal["next", "previous", "first"] | None = Field(
        default=None,
        description="Requested page movement for a retained canonical read",
    )
    balance_delta: BalanceFollowupDelta | None = Field(
        default=None,
        description="Sparse account-scope/operation patch for a retained balance conversation",
    )
    beneficiary_delta: BeneficiaryFollowupDelta | None = Field(
        default=None,
        description="Sparse operation/filter patch for a retained beneficiary read",
    )
    account_lifecycle_delta: AccountLifecycleFollowupDelta | None = Field(
        default=None,
        description="Sparse operation and bank-scope patch for a retained linked-account read",
    )
    set_scope_delta: SetScopeDelta | None = Field(
        default=None,
        description="Sparse set-scope patch resolved only against stable references in the retained frame",
    )
    set_amount_allocations: list[SetAmountAllocation] = Field(
        default_factory=list,
        max_length=5,
        description="Explicit per-beneficiary amounts; equal allocation must never be inferred",
    )
    schedule_edit_delta: ScheduleSetEditDelta | None = Field(
        default=None,
        description="Sparse explicit edit applied to every selected schedule; null outside schedule edits",
    )
    new_alias: str | None = Field(
        default=None,
        max_length=80,
        description="Explicit replacement beneficiary alias; null outside rename_beneficiary",
    )
    ticket_note: str | None = Field(
        default=None,
        max_length=1000,
        description="Explicit note text; null outside append_ticket_note",
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
    amount: MoneyAmount | None = Field(
        default=None,
        description="Deprecated absolute replacement amount; normalized to amount_mutation internally",
    )
    amount_mutation: AmountMutation | None = Field(
        default=None,
        description="Authoritative bounded mutation of the current pending transaction amount",
    )
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
    account_action: AccountActionHint | None = Field(
        default=None,
        description="Typed account action when switching to the account domain",
    )
    account_read: ReadRequest | None = Field(
        default=None,
        description="Canonical account read requested while another banking flow is pending",
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

    @model_validator(mode="after")
    def canonicalize_account_read_switch(self) -> "InterruptRouteDecision":
        if self.account_read is None:
            return self
        if self.account_read.subject not in {"balance", "linked_account", "default_account"}:
            raise ValueError("interrupt account_read must use an account read subject")
        self.decision = "switch_intent"
        self.target_intent = "account"
        self.target_mode = "new"
        self.status_query_type = None
        self.question_type = None
        self.target_field = None
        self.account_action = (
            "check_balance"
            if self.account_read.subject == "balance"
            else "get_default"
            if self.account_read.subject == "default_account"
            else "count"
            if self.account_read.response_shape in {"fact_bool", "fact_count"}
            else "list_accounts"
        )
        return self


class SemanticRouteDecision(BaseModel):
    """LLM decision for first-pass semantic routing before planner-owned dispatch."""

    model_config = ConfigDict(populate_by_name=True)

    decision: SemanticRoutingDecision = Field(default="planner_ambiguous", description="Top-level routing action")
    confidence: float = Field(default=0.0, alias="conf", description="Confidence in routing decision (0.0-1.0)")
    detected_language: str | None = Field(default=None, alias="lang", description="Detected language for this turn")
    requested_language: str | None = Field(
        default=None,
        alias="req_lang",
        description="Explicit language requested for switch when user asks to change locale.",
    )
    mode: SemanticRoutingMode | None = Field(
        default=None,
        description="Optional routing mode hint such as new-vs-continuation semantics.",
    )
    target_intent: RouterDomainIntent | None = Field(
        default=None,
        alias="intent",
        description="Optional normalized domain owner for observability/debugging.",
    )
    query_insight_type: QueryInsightType | None = Field(
        default=None,
        alias="q_insight",
        description="Typed analytical query subtype when the router can identify it confidently.",
    )
    response_key: SemanticRouterResponseKey | None = Field(
        default=None,
        alias="res_key",
        description="Deterministic keyed response when decision=direct_reply or cancel",
    )
    response: str | None = Field(
        default=None,
        alias="res",
        description="Direct response text when decision=direct_reply or direct_context_answer",
    )
    expected_transaction_executors: list[TransactionExecutor] = Field(
        default_factory=list,
        alias="execs",
        description="Explicit transaction executors expected from planner, when known",
    )
    read_request: ReadRequest | None = Field(
        default=None,
        alias="read",
        description="Canonical read subject, response shape, explicit filters, and page when this is a read turn.",
    )
    balance_contract: BalanceQueryContract | None = Field(
        default=None,
        alias="bal",
        description="Specialized account scope and operation for balance reads",
    )
    beneficiary_contract: BeneficiaryQueryContract | None = Field(
        default=None,
        alias="ben",
        description="Specialized filters and operation for beneficiary reads",
    )
    schedule_contract: ScheduleQueryContract | None = Field(
        default=None,
        alias="sched",
        description="Specialized filters and operation for schedule reads",
    )
    account_lifecycle_contract: AccountLifecycleContract | None = Field(
        default=None,
        alias="acct",
        description="Specialized filters and operation for linked-account lifecycle reads",
    )
    # This is an internal, already-grounded continuation decision.  It is not
    # part of the broad semantic-router provider schema: the provider returns a
    # deliberately narrow wire representation which is adapted below.  Keeping
    # the runtime value here lets the gate materialize one canonical route
    # without starting a second context-frame LLM call.
    context_followup: ContextFrameFollowupDecision | None = Field(
        default=None,
        exclude=True,
        description="Optional typed continuation against an eligible displayed frame.",
    )
    context_replay_modifier: ContextFrameReplayModifier | None = Field(
        default=None,
        exclude=True,
        description="Optional explicit replay patch returned with a context continuation.",
    )
    unsupported_capability: str | None = Field(
        default=None,
        alias="unsupported_cap",
        description="Optional key of the detected unsupported capability, else null",
    )

    @model_validator(mode="before")
    @classmethod
    def complete_specialized_read_contract(cls, value: Any) -> Any:
        """Complete redundant domain contracts from the canonical semantic read.

        The semantic model remains authoritative for subject, shape, and explicit
        filters. This adapter only maps that typed information into the narrower
        worker contract, avoiding a second interpretation or a raw-text fallback.
        """
        if not isinstance(value, dict):
            return value
        raw_request = value.get("read_request", value.get("read"))
        try:
            request = (
                raw_request
                if isinstance(raw_request, ReadRequest)
                else ReadRequest.model_validate(raw_request)
                if isinstance(raw_request, dict)
                else None
            )
        except ValueError:
            return value
        if request is None:
            return value

        completed = dict(value)
        if request.subject == "balance" and completed.get("balance_contract", completed.get("bal")) is None:
            completed["bal"] = initial_balance_contract(
                bank_name=request.bank_name,
                response_shape=request.response_shape,
            )
        elif request.subject == "beneficiary" and completed.get(
            "beneficiary_contract", completed.get("ben")
        ) is None:
            operation: Literal["count", "existence", "list", "detail"] = "list"
            if request.response_shape == "fact_count":
                operation = "count"
            elif request.response_shape == "fact_bool":
                operation = "existence"
            elif request.response_shape == "surface_detail":
                operation = "detail"
            completed["ben"] = BeneficiaryQueryContract(
                operation=operation,
                response_shape=request.response_shape,
                entity_name=request.entity_name,
                bank_name=request.bank_name,
            )
        elif request.subject == "schedule" and completed.get("schedule_contract", completed.get("sched")) is None:
            schedule_operation: Literal["count", "existence", "list", "detail"] = "list"
            if request.response_shape == "fact_count":
                schedule_operation = "count"
            elif request.response_shape == "fact_bool":
                schedule_operation = "existence"
            elif request.response_shape in {"fact_status", "surface_detail"}:
                schedule_operation = "detail"
            completed["sched"] = ScheduleQueryContract(
                operation=schedule_operation,
                response_shape=request.response_shape,
                recipient_name=request.entity_name,
                statuses=[request.status] if request.status else [],
            )
        elif request.subject in {"linked_account", "default_account"} and completed.get(
            "account_lifecycle_contract", completed.get("acct")
        ) is None:
            account_operation: Literal[
                "count", "existence", "list", "detail", "readiness", "default_identity"
            ] = "default_identity" if request.subject == "default_account" else "list"
            if request.subject == "linked_account":
                if request.response_shape == "fact_count":
                    account_operation = "count"
                elif request.response_shape == "fact_bool":
                    account_operation = "existence"
                elif request.response_shape == "fact_status":
                    account_operation = "readiness"
                elif request.response_shape == "surface_detail":
                    account_operation = "detail"
            completed["acct"] = AccountLifecycleContract(
                operation=account_operation,
                response_shape=request.response_shape,
                bank_name=request.bank_name,
                mandate_statuses=[request.status] if request.status else [],
            )
        return completed

    @model_validator(mode="after")
    def require_specialized_read_contract(self) -> "SemanticRouteDecision":
        request = self.read_request
        if request is None:
            return self
        required_contract = {
            "balance": self.balance_contract,
            "beneficiary": self.beneficiary_contract,
            "schedule": self.schedule_contract,
            "linked_account": self.account_lifecycle_contract,
            "default_account": self.account_lifecycle_contract,
        }.get(request.subject, True)
        if required_contract is None:
            raise ValueError(f"{request.subject} reads require their specialized contract")
        return self


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
        default=None, description="Detected language: English, Yoruba, Hausa, Igbo, or Pidgin"
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
            "list/list_accounts/count/check_balance/get_default/link/unlink/set_default, else none"
        ),
    )
    unsupported_capability: str | None = Field(
        default=None,
        description="Optional key of the detected unsupported capability, else null",
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
