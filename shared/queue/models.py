"""Models for Redis Queue payloads."""

from typing import Any, Literal, NotRequired, TypedDict


class ReceiptTransferDataRecipient(TypedDict):
    name: str | None
    account_number: str | None
    bank_name: str | None


class ReceiptTransferDataSource(TypedDict):
    name: str | None
    account_name: str | None
    account_number: NotRequired[str | None]


class ReceiptTransferData(TypedDict):
    amount: float | str | None
    source: ReceiptTransferDataSource
    recipient: ReceiptTransferDataRecipient
    narration: str | None
    channel: NotRequired[str | None]
    session_id: NotRequired[str | None]
    processor_name: NotRequired[str | None]


class ReceiptJobPayload(TypedDict):
    phone_number: str
    channel: str
    channel_identity: str | None
    transfer_data: ReceiptTransferData
    transaction_reference: str | None
    signal_key: str
    beneficiary_suggestion_message: NotRequired[str]


class TransferScheduledMeta(TypedDict):
    schedule_id: str
    schedule_run_id: str
    run_source: Literal["scheduled"]
    attempt: int
    channel: str
    channel_identity: NotRequired[str | None]


class TransferJobPayload(TypedDict):
    type: Literal["execute_transfer"]
    idempotency_key: str
    transaction_id: str
    phone_number: str
    transfer_data: dict[str, Any]
    scheduled_meta: NotRequired[TransferScheduledMeta]


class RefundJobPayload(TypedDict):
    funding_step_id: str
    funded_transfer_id: str
    amount: float
    account_id: str
    original_reference: str


class PayoutJobPayload(TypedDict):
    funded_transfer_id: str
    amount: float
    recipient_account: str
    recipient_bank_code: str
    idempotency_key: str
    narration: NotRequired[str | None]


class FundingJobPayload(TypedDict):
    type: Literal["initiate_funding"]
    funded_transfer_id: str
    idempotency_key: str
    transaction_id: str | None
    narration: str | None


class AirtimeRecipient(TypedDict):
    phone: str
    network: str
    name: str


class AirtimeSource(TypedDict):
    id: str | None
    account_number: str
    bank_name: str


class AirtimeData(TypedDict):
    amount: float
    recipient: AirtimeRecipient
    source: AirtimeSource
    narration: str


class AirtimeJobPayload(TypedDict):
    type: Literal["execute_airtime"]
    phone_number: str
    idempotency_key: str
    airtime_data: AirtimeData
    transaction_id: str
    channel: str


class DataPurchaseData(TypedDict):
    plan_code: str
    plan_name: str
    amount: float
    target_phone: str
    network: str
    source: str


class DataJobPayload(TypedDict):
    type: Literal["execute_data"]
    phone_number: str
    idempotency_key: str
    data_purchase: DataPurchaseData
    transaction_id: str
    channel: str


class FlowEventPayload(TypedDict):
    event_type: str
    phone_number: str
    flow_type: str
    idempotency_key: str
    success: bool
    error: NotRequired[str]
    extra_data: NotRequired[dict[str, Any] | None]
    channel: NotRequired[str]
