"""encrypt_bank_identifiers

Revision ID: c7e8a9b0d1f2
Revises: b3d5f7a9c1e2
Create Date: 2026-06-01 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from shared.database.enums import BeneficiaryTypeEnum
from shared.security.field_encryption import (
    decrypt_optional,
    encrypt_account_number,
    encrypt_secret_identifier,
    require_field_encryption_ready,
)

# revision identifiers, used by Alembic.
revision: str = "c7e8a9b0d1f2"
down_revision: str | None = "b3d5f7a9c1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    require_field_encryption_ready()
    _add_columns()
    _backfill_encrypted_values()
    _create_indexes()


def downgrade() -> None:
    require_field_encryption_ready()
    _decrypt_values()
    _drop_indexes()
    _drop_columns()


def _add_columns() -> None:
    op.add_column("accounts", sa.Column("account_number_blind_index", sa.String(), nullable=True))
    op.add_column("accounts", sa.Column("account_number_last4", sa.String(length=4), nullable=True))
    op.add_column("accounts", sa.Column("mandate_id_blind_index", sa.String(), nullable=True))

    op.add_column("beneficiaries", sa.Column("account_number_blind_index", sa.String(), nullable=True))
    op.add_column("beneficiaries", sa.Column("account_number_last4", sa.String(length=4), nullable=True))

    op.add_column("transactions", sa.Column("source_account_number_blind_index", sa.String(), nullable=True))
    op.add_column("transactions", sa.Column("source_account_number_last4", sa.String(length=4), nullable=True))
    op.add_column("transactions", sa.Column("recipient_account_number_blind_index", sa.String(), nullable=True))
    op.add_column("transactions", sa.Column("recipient_account_number_last4", sa.String(length=4), nullable=True))

    op.add_column("funded_transfers", sa.Column("recipient_account_number_blind_index", sa.String(), nullable=True))
    op.add_column("funded_transfers", sa.Column("recipient_account_number_last4", sa.String(length=4), nullable=True))


def _create_indexes() -> None:
    op.create_index("ix_accounts_account_number_blind_index", "accounts", ["account_number_blind_index"])
    op.create_index("ix_accounts_account_number_last4", "accounts", ["account_number_last4"])
    op.create_index("ix_accounts_mandate_id_blind_index", "accounts", ["mandate_id_blind_index"])

    op.create_index("ix_beneficiaries_account_number_blind_index", "beneficiaries", ["account_number_blind_index"])
    op.create_index("ix_beneficiaries_account_number_last4", "beneficiaries", ["account_number_last4"])

    op.create_index(
        "ix_transactions_source_account_number_blind_index",
        "transactions",
        ["source_account_number_blind_index"],
    )
    op.create_index("ix_transactions_source_account_number_last4", "transactions", ["source_account_number_last4"])
    op.create_index(
        "ix_transactions_recipient_account_number_blind_index",
        "transactions",
        ["recipient_account_number_blind_index"],
    )
    op.create_index(
        "ix_transactions_recipient_account_number_last4",
        "transactions",
        ["recipient_account_number_last4"],
    )

    op.create_index(
        "ix_funded_transfers_recipient_account_number_blind_index",
        "funded_transfers",
        ["recipient_account_number_blind_index"],
    )
    op.create_index(
        "ix_funded_transfers_recipient_account_number_last4",
        "funded_transfers",
        ["recipient_account_number_last4"],
    )


def _drop_indexes() -> None:
    op.drop_index("ix_funded_transfers_recipient_account_number_last4", table_name="funded_transfers")
    op.drop_index("ix_funded_transfers_recipient_account_number_blind_index", table_name="funded_transfers")
    op.drop_index("ix_transactions_recipient_account_number_last4", table_name="transactions")
    op.drop_index("ix_transactions_recipient_account_number_blind_index", table_name="transactions")
    op.drop_index("ix_transactions_source_account_number_last4", table_name="transactions")
    op.drop_index("ix_transactions_source_account_number_blind_index", table_name="transactions")
    op.drop_index("ix_beneficiaries_account_number_last4", table_name="beneficiaries")
    op.drop_index("ix_beneficiaries_account_number_blind_index", table_name="beneficiaries")
    op.drop_index("ix_accounts_mandate_id_blind_index", table_name="accounts")
    op.drop_index("ix_accounts_account_number_last4", table_name="accounts")
    op.drop_index("ix_accounts_account_number_blind_index", table_name="accounts")


def _drop_columns() -> None:
    op.drop_column("funded_transfers", "recipient_account_number_last4")
    op.drop_column("funded_transfers", "recipient_account_number_blind_index")
    op.drop_column("transactions", "recipient_account_number_last4")
    op.drop_column("transactions", "recipient_account_number_blind_index")
    op.drop_column("transactions", "source_account_number_last4")
    op.drop_column("transactions", "source_account_number_blind_index")
    op.drop_column("beneficiaries", "account_number_last4")
    op.drop_column("beneficiaries", "account_number_blind_index")
    op.drop_column("accounts", "mandate_id_blind_index")
    op.drop_column("accounts", "account_number_last4")
    op.drop_column("accounts", "account_number_blind_index")


def _backfill_encrypted_values() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    accounts = sa.Table("accounts", metadata, autoload_with=bind)
    beneficiaries = sa.Table("beneficiaries", metadata, autoload_with=bind)
    transactions = sa.Table("transactions", metadata, autoload_with=bind)
    funded_transfers = sa.Table("funded_transfers", metadata, autoload_with=bind)

    for row in bind.execute(sa.select(accounts.c.id, accounts.c.account_number, accounts.c.mandate_id)):
        account_number = encrypt_account_number(row.account_number, field="accounts.account_number")
        mandate_id = encrypt_secret_identifier(row.mandate_id, field="accounts.mandate_id")
        bind.execute(
            accounts.update()
            .where(accounts.c.id == row.id)
            .values(
                account_number=account_number.ciphertext,
                account_number_blind_index=account_number.blind_index,
                account_number_last4=account_number.last4,
                mandate_id=mandate_id.ciphertext or None,
                mandate_id_blind_index=mandate_id.blind_index,
            )
        )

    for row in bind.execute(
        sa.select(beneficiaries.c.id, beneficiaries.c.beneficiary_type, beneficiaries.c.account_number)
    ):
        if row.beneficiary_type == BeneficiaryTypeEnum.TRANSFER.value:
            account_number = encrypt_account_number(row.account_number, field="beneficiaries.account_number")
            values = {
                "account_number": account_number.ciphertext or None,
                "account_number_blind_index": account_number.blind_index,
                "account_number_last4": account_number.last4,
            }
        else:
            raw = str(row.account_number or "").strip()
            values = {
                "account_number": raw or None,
                "account_number_blind_index": None,
                "account_number_last4": raw[-4:] if raw else None,
            }
        bind.execute(beneficiaries.update().where(beneficiaries.c.id == row.id).values(**values))

    for row in bind.execute(
        sa.select(
            transactions.c.id,
            transactions.c.source_account_number,
            transactions.c.recipient_account_number,
        )
    ):
        source = encrypt_account_number(row.source_account_number, field="transactions.source_account_number")
        recipient = encrypt_account_number(row.recipient_account_number, field="transactions.recipient_account_number")
        bind.execute(
            transactions.update()
            .where(transactions.c.id == row.id)
            .values(
                source_account_number=source.ciphertext,
                source_account_number_blind_index=source.blind_index,
                source_account_number_last4=source.last4,
                recipient_account_number=recipient.ciphertext or None,
                recipient_account_number_blind_index=recipient.blind_index,
                recipient_account_number_last4=recipient.last4,
            )
        )

    for row in bind.execute(sa.select(funded_transfers.c.id, funded_transfers.c.recipient_account_number)):
        recipient = encrypt_account_number(
            row.recipient_account_number,
            field="funded_transfers.recipient_account_number",
        )
        bind.execute(
            funded_transfers.update()
            .where(funded_transfers.c.id == row.id)
            .values(
                recipient_account_number=recipient.ciphertext,
                recipient_account_number_blind_index=recipient.blind_index,
                recipient_account_number_last4=recipient.last4,
            )
        )


def _decrypt_values() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    accounts = sa.Table("accounts", metadata, autoload_with=bind)
    beneficiaries = sa.Table("beneficiaries", metadata, autoload_with=bind)
    transactions = sa.Table("transactions", metadata, autoload_with=bind)
    funded_transfers = sa.Table("funded_transfers", metadata, autoload_with=bind)

    for row in bind.execute(sa.select(accounts.c.id, accounts.c.account_number, accounts.c.mandate_id)):
        bind.execute(
            accounts.update()
            .where(accounts.c.id == row.id)
            .values(
                account_number=decrypt_optional(row.account_number) or "",
                mandate_id=decrypt_optional(row.mandate_id),
            )
        )

    for row in bind.execute(sa.select(beneficiaries.c.id, beneficiaries.c.account_number)):
        bind.execute(
            beneficiaries.update()
            .where(beneficiaries.c.id == row.id)
            .values(account_number=decrypt_optional(row.account_number))
        )

    for row in bind.execute(
        sa.select(transactions.c.id, transactions.c.source_account_number, transactions.c.recipient_account_number)
    ):
        bind.execute(
            transactions.update()
            .where(transactions.c.id == row.id)
            .values(
                source_account_number=decrypt_optional(row.source_account_number) or "",
                recipient_account_number=decrypt_optional(row.recipient_account_number),
            )
        )

    for row in bind.execute(sa.select(funded_transfers.c.id, funded_transfers.c.recipient_account_number)):
        bind.execute(
            funded_transfers.update()
            .where(funded_transfers.c.id == row.id)
            .values(recipient_account_number=decrypt_optional(row.recipient_account_number) or "")
        )
