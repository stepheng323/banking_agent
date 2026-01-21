"""Models for Redis Queue payloads."""

from typing import Any, Literal, TypedDict


class ReceiptTransferDataRecipient(TypedDict):
    name: str | None
    account_number: str | None
    bank_name: str | None


class ReceiptTransferDataSource(TypedDict):
    name: str | None
    account_name: str | None


class ReceiptTransferData(TypedDict):
    amount: float | str | None
    source: ReceiptTransferDataSource
    recipient: ReceiptTransferDataRecipient
    narration: str | None


class ReceiptJobPayload(TypedDict):
    phone_number: str
    transfer_data: ReceiptTransferData
    transaction_reference: str | None
    signal_key: str


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


class FlowEventPayload(TypedDict):
    event_type: str
    phone_number: str
    flow_type: str
    idempotency_key: str
    success: bool
    error: str | None
    extra_data: dict[str, Any] | None
