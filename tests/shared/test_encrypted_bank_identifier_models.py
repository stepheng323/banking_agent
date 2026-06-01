"""Tests for encrypted bank identifier ORM fields."""

from decimal import Decimal
from uuid import uuid4

from shared.database.enums import BeneficiaryTypeEnum, FundedTransferStatusEnum, TransactionStatusEnum
from shared.database.models import Account, Beneficiary, FundedTransfer, Transaction
from shared.security.field_encryption import is_encrypted_value


def test_account_number_and_mandate_store_ciphertext_in_existing_columns() -> None:
    account = Account(
        user_id=uuid4(),
        account_id="mono_acc_1",
        account_number="1234567890",
        bank_name="Test Bank",
        bank_code="001",
        account_name="Ada Lovelace",
    )
    account.mandate_id = "mandate_abc"

    assert account.account_number == "1234567890"
    assert account.mandate_id == "mandate_abc"
    assert is_encrypted_value(account._account_number_ciphertext)
    assert is_encrypted_value(account._mandate_id_ciphertext)
    assert account.account_number_blind_index
    assert account.mandate_id_blind_index
    assert account.account_number_last4 == "7890"
    assert "1234567890" not in repr(account)
    assert "mandate_abc" not in repr(account)


def test_transfer_beneficiary_account_number_is_encrypted_but_mobile_phone_stays_plaintext() -> None:
    transfer = Beneficiary(
        user_id=uuid4(),
        beneficiary_type=BeneficiaryTypeEnum.TRANSFER.value,
        account_name="Grace Hopper",
        account_number="0123456789",
        bank_code="044",
        bank_name="Access Bank",
    )
    airtime = Beneficiary(
        user_id=uuid4(),
        beneficiary_type=BeneficiaryTypeEnum.AIRTIME.value,
        account_name="Self",
        account_number="08012345678",
        bank_name="MTN",
    )

    assert transfer.account_number == "0123456789"
    assert is_encrypted_value(transfer._account_number_ciphertext)
    assert transfer.account_number_blind_index
    assert transfer.account_number_last4 == "6789"
    assert "0123456789" not in repr(transfer)

    assert airtime.account_number == "08012345678"
    assert airtime._account_number_ciphertext == "08012345678"
    assert airtime.account_number_blind_index is None
    assert airtime.account_number_last4 == "5678"


def test_transaction_account_numbers_store_ciphertext_with_lookup_helpers() -> None:
    transaction = Transaction(
        user_id=uuid4(),
        transaction_type="transfer",
        status=TransactionStatusEnum.PENDING.value,
        amount=Decimal("1200.00"),
        currency="NGN",
        source_account_number="1234567890",
        source_bank_name="GTBank",
        recipient_account_number="0123456789",
        recipient_bank_code="044",
        recipient_bank_name="Access Bank",
        recipient_name="Grace Hopper",
        idempotency_key="idem-tx-1",
    )

    assert transaction.source_account_number == "1234567890"
    assert transaction.recipient_account_number == "0123456789"
    assert is_encrypted_value(transaction._source_account_number_ciphertext)
    assert is_encrypted_value(transaction._recipient_account_number_ciphertext)
    assert transaction.source_account_number_last4 == "7890"
    assert transaction.recipient_account_number_last4 == "6789"


def test_funded_transfer_recipient_account_number_store_ciphertext() -> None:
    transfer = FundedTransfer(
        user_id=uuid4(),
        amount=Decimal("5000.00"),
        currency="NGN",
        recipient_account_number="0123456789",
        recipient_bank_code="044",
        recipient_bank_name="Access Bank",
        recipient_name="Grace Hopper",
        status=FundedTransferStatusEnum.DRAFT.value,
        idempotency_key="idem-funded-1",
    )

    assert transfer.recipient_account_number == "0123456789"
    assert is_encrypted_value(transfer._recipient_account_number_ciphertext)
    assert transfer.recipient_account_number_blind_index
    assert transfer.recipient_account_number_last4 == "6789"
