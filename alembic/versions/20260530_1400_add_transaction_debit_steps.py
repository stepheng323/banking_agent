"""add_transaction_debit_steps

Revision ID: b3d5f7a9c1e2
Revises: a2c4d6e8f0b1
Create Date: 2026-05-30 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d5f7a9c1e2"
down_revision: str | None = "a2c4d6e8f0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_TYPE = postgresql.UUID(as_uuid=True)
MONEY_TYPE = sa.Numeric(18, 2)


def upgrade() -> None:
    op.create_table(
        "transaction_debit_steps",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("transaction_id", UUID_TYPE, nullable=False),
        sa.Column("account_id", UUID_TYPE, nullable=False),
        sa.Column("amount", MONEY_TYPE, nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="NGN"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("provider_name", sa.String(), nullable=True),
        sa.Column("provider_debit_id", sa.String(), nullable=True),
        sa.Column("provider_reference", sa.String(), nullable=True),
        sa.Column("refund_provider_id", sa.String(), nullable=True),
        sa.Column("refund_provider_reference", sa.String(), nullable=True),
        sa.Column("initiated_at", sa.DateTime(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(), nullable=True),
        sa.Column("refund_initiated_at", sa.DateTime(), nullable=True),
        sa.Column("refund_last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refund_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refund_error_message", sa.String(), nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_transaction_debit_steps_positive_amount"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name="fk_transaction_debit_steps_account_id"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_transaction_debit_steps_transaction_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_id", name="uq_transaction_debit_steps_transaction_id"),
        sa.UniqueConstraint("provider_debit_id", name="uq_transaction_debit_steps_provider_debit_id"),
        sa.UniqueConstraint("provider_reference", name="uq_transaction_debit_steps_provider_reference"),
    )
    op.create_index(op.f("ix_transaction_debit_steps_id"), "transaction_debit_steps", ["id"], unique=False)
    op.create_index(
        op.f("ix_transaction_debit_steps_transaction_id"),
        "transaction_debit_steps",
        ["transaction_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_transaction_debit_steps_account_id"),
        "transaction_debit_steps",
        ["account_id"],
        unique=False,
    )
    op.create_index(op.f("ix_transaction_debit_steps_status"), "transaction_debit_steps", ["status"], unique=False)
    op.create_index(
        op.f("ix_transaction_debit_steps_provider_debit_id"),
        "transaction_debit_steps",
        ["provider_debit_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_transaction_debit_steps_provider_reference"),
        "transaction_debit_steps",
        ["provider_reference"],
        unique=True,
    )
    op.create_index(
        op.f("ix_transaction_debit_steps_refund_provider_id"),
        "transaction_debit_steps",
        ["refund_provider_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transaction_debit_steps_refund_provider_reference"),
        "transaction_debit_steps",
        ["refund_provider_reference"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_transaction_debit_steps_refund_provider_reference"), table_name="transaction_debit_steps")
    op.drop_index(op.f("ix_transaction_debit_steps_refund_provider_id"), table_name="transaction_debit_steps")
    op.drop_index(op.f("ix_transaction_debit_steps_provider_reference"), table_name="transaction_debit_steps")
    op.drop_index(op.f("ix_transaction_debit_steps_provider_debit_id"), table_name="transaction_debit_steps")
    op.drop_index(op.f("ix_transaction_debit_steps_status"), table_name="transaction_debit_steps")
    op.drop_index(op.f("ix_transaction_debit_steps_account_id"), table_name="transaction_debit_steps")
    op.drop_index(op.f("ix_transaction_debit_steps_transaction_id"), table_name="transaction_debit_steps")
    op.drop_index(op.f("ix_transaction_debit_steps_id"), table_name="transaction_debit_steps")
    op.drop_table("transaction_debit_steps")
