from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from banking.transactions.services.unified_transactions import UnifiedTransactionService


def _local(**overrides):
    base = {
        "id": uuid4(),
        "transaction_type": "transfer",
        "status": "successful",
        "amount": 5000.0,
        "currency": "NGN",
        "recipient_name": "Tolu Adebayo",
        "recipient_account_number": "1234567890",
        "recipient_bank_name": "Kuda",
        "recipient_bank_code": "999999",
        "source_bank_name": "GTBank",
        "source_account_number": "0123456789",
        "transaction_id": "debit-123",
        "idempotency_key": "idem-123",
        "provider_response": {"reference": "debit-123"},
        "created_at": datetime(2026, 5, 16, 9, 0, 0),
        "updated_at": datetime(2026, 5, 16, 9, 1, 0),
        "completed_at": datetime(2026, 5, 16, 9, 2, 0),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _bank(**overrides):
    base = {
        "provider_transaction_id": "debit-123",
        "amount": 5000.0,
        "currency": "NGN",
        "transaction_type": "debit",
        "narration": "TRANSFER TO TOLU ADEBAYO",
        "counterparty": "Tolu Adebayo",
        "bank_name": "GTBank",
        "posted_at": datetime(2026, 5, 16, 9, 3, 0),
        "posted_date": datetime(2026, 5, 16).date(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_unified_reconciliation_exact_provider_reference_match() -> None:
    records = UnifiedTransactionService.reconcile([_local()], [_bank()])

    assert len(records) == 1
    record = records[0]
    assert record.source == "reconciled"
    assert record.match_confidence == "exact"
    assert record.display_status == "successful"
    assert record.local_status == "successful"
    assert record.bank_status == "posted"


def test_unified_reconciliation_strong_amount_date_counterparty_match() -> None:
    local = _local(transaction_id="local-provider-ref", provider_response={}, status="processing")
    bank = _bank(provider_transaction_id="bank-only-ref", posted_at=local.created_at + timedelta(hours=1))

    records = UnifiedTransactionService.reconcile([local], [bank])

    assert len(records) == 1
    record = records[0]
    assert record.source == "reconciled"
    assert record.match_confidence == "strong"
    assert record.display_status == "processing"
    assert record.bank_status == "posted"


def test_unified_reconciliation_does_not_match_different_recipient() -> None:
    local = _local(transaction_id="local-provider-ref", provider_response={}, recipient_name="Ada")
    bank = _bank(provider_transaction_id="bank-only-ref", counterparty="Tolu Adebayo")

    records = UnifiedTransactionService.reconcile([local], [bank])

    assert len(records) == 2
    assert {record.source for record in records} == {"local", "bank"}


def test_unified_status_precedence_for_local_and_bank_rows() -> None:
    bank_only = UnifiedTransactionService.reconcile([], [_bank()])[0]
    local_only = UnifiedTransactionService.reconcile([_local(status="pending")], [])[0]
    failed_reconciled = UnifiedTransactionService.reconcile(
        [_local(status="failed", error_message="Provider timeout")],
        [_bank()],
    )[0]

    assert bank_only.display_status == "posted"
    assert bank_only.actionable == {"retry": False, "receipt": False, "escalate": True}
    assert local_only.display_status == "pending"
    assert failed_reconciled.display_status == "failed"
    assert failed_reconciled.needs_review is True
    assert failed_reconciled.actionable["retry"] is False


def test_unified_local_airtime_uses_mobile_fields_for_display() -> None:
    record = UnifiedTransactionService.reconcile(
        [
            _local(
                transaction_type="airtime",
                amount=2000,
                recipient_name=None,
                recipient_account_number=None,
                recipient_bank_name=None,
                recipient_bank_code=None,
                target_phone_number="08031234567",
                mobile_network="MTN",
                narration="Airtime top-up",
            )
        ],
        [],
    )[0]

    query = record.to_query_dict()

    assert query["counterparty"] == "08031234567 (MTN)"
    assert query["recipient_name"] == "08031234567 (MTN)"
    assert query["target_phone_number"] == "08031234567"
    assert query["mobile_network"] == "MTN"
    assert query["recipient_account_number"] is None
    assert query["recipient_bank_name"] is None
    assert query["recipient_bank_code"] is None


def test_unified_local_airtime_uses_saved_mobile_name_for_display() -> None:
    record = UnifiedTransactionService.reconcile(
        [
            _local(
                transaction_type="airtime",
                amount=2000,
                recipient_name=None,
                recipient_account_number=None,
                recipient_bank_name=None,
                recipient_bank_code=None,
                target_phone_number="08031234567",
                mobile_network="MTN",
                service_metadata={"recipient_name": "Tolu"},
                narration="Airtime top-up",
            )
        ],
        [],
    )[0]

    query = record.to_query_dict()

    assert query["counterparty"] == "Tolu (08031234567, MTN)"
    assert query["recipient_name"] == "Tolu"
    assert query["target_phone_number"] == "08031234567"
    assert query["mobile_network"] == "MTN"


def test_unified_local_data_uses_biller_fields_for_display() -> None:
    record = UnifiedTransactionService.reconcile(
        [
            _local(
                transaction_type="data",
                amount=3500,
                recipient_name=None,
                recipient_account_number=None,
                recipient_bank_name=None,
                recipient_bank_code=None,
                target_phone_number="08162511023",
                mobile_network="MTN",
                biller_code="BIL104",
                biller_item_code="MD501",
                biller_item_name="MTN 5 GB data bundle",
                service_metadata={"size_gb": 5.0, "validity_days": 30},
                narration="Data purchase",
            )
        ],
        [],
    )[0]

    query = record.to_query_dict()

    assert query["counterparty"] == "MTN 5 GB data bundle for 08162511023"
    assert query["recipient_name"] == "MTN 5 GB data bundle for 08162511023"
    assert query["target_phone_number"] == "08162511023"
    assert query["mobile_network"] == "MTN"
    assert query["biller_code"] == "BIL104"
    assert query["biller_item_code"] == "MD501"
    assert query["biller_item_name"] == "MTN 5 GB data bundle"
    assert query["service_metadata"] == {"size_gb": 5.0, "validity_days": 30}
    assert query["recipient_account_number"] is None
    assert query["recipient_bank_name"] is None
    assert query["recipient_bank_code"] is None


def test_unified_local_data_uses_mobile_recipient_name_for_display() -> None:
    record = UnifiedTransactionService.reconcile(
        [
            _local(
                transaction_type="data",
                amount=3500,
                recipient_name=None,
                recipient_account_number=None,
                recipient_bank_name=None,
                recipient_bank_code=None,
                target_phone_number="08162511023",
                mobile_network="MTN",
                biller_code="BIL104",
                biller_item_code="MD501",
                biller_item_name="MTN 5 GB data bundle",
                service_metadata={"recipient_name": "Tolu", "size_gb": 5.0, "validity_days": 30},
                narration="Data purchase",
            )
        ],
        [],
    )[0]

    query = record.to_query_dict()

    assert query["counterparty"] == "MTN 5 GB data bundle for Tolu (08162511023, MTN)"
    assert query["recipient_name"] == "MTN 5 GB data bundle for Tolu (08162511023, MTN)"
    assert query["target_phone_number"] == "08162511023"
    assert query["mobile_network"] == "MTN"


def test_unified_local_data_requires_mobile_biller_fields_for_display() -> None:
    record = UnifiedTransactionService.reconcile(
        [
            _local(
                transaction_type="data",
                recipient_name="MTN 5 GB data bundle",
                recipient_account_number="08162511023",
                recipient_bank_name="MTN",
                recipient_bank_code="MTN",
                target_phone_number=None,
                mobile_network=None,
                biller_item_name=None,
            )
        ],
        [],
    )[0]

    query = record.to_query_dict()

    assert query["counterparty"] is None
    assert query["target_phone_number"] is None
    assert query["mobile_network"] is None
    assert query["biller_item_name"] is None
    assert query["recipient_account_number"] is None
