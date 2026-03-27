"""add_bank_transaction_mirror_tables

Revision ID: c7f6a7b6b0c1
Revises: 8a69f2dc1b2f
Create Date: 2026-03-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7f6a7b6b0c1"
down_revision: str | None = "8a69f2dc1b2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bank_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("linked_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_transaction_id", sa.String(), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=False),
        sa.Column("posted_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("transaction_type", sa.String(), nullable=False),
        sa.Column("narration", sa.Text(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("counterparty", sa.String(), nullable=True),
        sa.Column("counterparty_role", sa.String(), nullable=True),
        sa.Column("counterparty_source", sa.String(), nullable=True),
        sa.Column("resolved_category", sa.String(), nullable=True),
        sa.Column("category_source", sa.String(), nullable=True),
        sa.Column("parser_rule", sa.String(), nullable=True),
        sa.Column("bank_name", sa.String(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["linked_account_id"], ["accounts.id"], name="fk_bank_transactions_linked_account_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_bank_transactions_user_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "linked_account_id",
            "provider",
            "provider_transaction_id",
            name="uq_bank_transactions_provider_txn",
        ),
    )
    op.create_index(op.f("ix_bank_transactions_id"), "bank_transactions", ["id"], unique=False)
    op.create_index(op.f("ix_bank_transactions_user_id"), "bank_transactions", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_bank_transactions_linked_account_id"),
        "bank_transactions",
        ["linked_account_id"],
        unique=False,
    )
    op.create_index(op.f("ix_bank_transactions_provider"), "bank_transactions", ["provider"], unique=False)
    op.create_index(op.f("ix_bank_transactions_posted_at"), "bank_transactions", ["posted_at"], unique=False)
    op.create_index(op.f("ix_bank_transactions_posted_date"), "bank_transactions", ["posted_date"], unique=False)
    op.create_index(op.f("ix_bank_transactions_counterparty"), "bank_transactions", ["counterparty"], unique=False)
    op.create_index(
        op.f("ix_bank_transactions_resolved_category"),
        "bank_transactions",
        ["resolved_category"],
        unique=False,
    )
    op.create_index(
        "ix_bank_transactions_user_id_posted_at_desc",
        "bank_transactions",
        ["user_id", sa.text("posted_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_bank_transactions_account_id_posted_at_desc",
        "bank_transactions",
        ["linked_account_id", sa.text("posted_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_bank_transactions_account_type_posted_at_desc",
        "bank_transactions",
        ["linked_account_id", "transaction_type", sa.text("posted_at DESC")],
        unique=False,
    )

    op.create_table(
        "bank_transaction_coverage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("linked_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("coverage_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["linked_account_id"],
            ["accounts.id"],
            name="fk_bank_transaction_coverage_linked_account_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bank_transaction_coverage_id"), "bank_transaction_coverage", ["id"], unique=False)
    op.create_index(
        op.f("ix_bank_transaction_coverage_linked_account_id"),
        "bank_transaction_coverage",
        ["linked_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bank_transaction_coverage_provider"),
        "bank_transaction_coverage",
        ["provider"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("bank_transaction_coverage")
    op.drop_table("bank_transactions")
