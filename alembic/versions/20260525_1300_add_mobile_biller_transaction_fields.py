"""add_mobile_biller_transaction_fields

Revision ID: 4b4cb47c7c1f
Revises: c7f6a7b6b0c1
Create Date: 2026-05-25 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b4cb47c7c1f"
down_revision: str | None = "c7f6a7b6b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("target_phone_number", sa.String(), nullable=True))
    op.add_column("transactions", sa.Column("mobile_network", sa.String(), nullable=True))
    op.add_column("transactions", sa.Column("biller_code", sa.String(), nullable=True))
    op.add_column("transactions", sa.Column("biller_item_code", sa.String(), nullable=True))
    op.add_column("transactions", sa.Column("biller_item_name", sa.String(), nullable=True))
    op.add_column("transactions", sa.Column("service_metadata", sa.JSON(), nullable=True))
    op.alter_column("transactions", "recipient_account_number", existing_type=sa.String(), nullable=True)
    op.alter_column("transactions", "recipient_bank_code", existing_type=sa.String(), nullable=True)
    op.alter_column("transactions", "recipient_bank_name", existing_type=sa.String(), nullable=True)
    op.alter_column("transactions", "recipient_name", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE transactions SET recipient_account_number = '' WHERE recipient_account_number IS NULL")
    op.execute("UPDATE transactions SET recipient_bank_code = '' WHERE recipient_bank_code IS NULL")
    op.execute("UPDATE transactions SET recipient_bank_name = '' WHERE recipient_bank_name IS NULL")
    op.execute("UPDATE transactions SET recipient_name = '' WHERE recipient_name IS NULL")
    op.alter_column("transactions", "recipient_name", existing_type=sa.String(), nullable=False)
    op.alter_column("transactions", "recipient_bank_name", existing_type=sa.String(), nullable=False)
    op.alter_column("transactions", "recipient_bank_code", existing_type=sa.String(), nullable=False)
    op.alter_column("transactions", "recipient_account_number", existing_type=sa.String(), nullable=False)
    op.drop_column("transactions", "service_metadata")
    op.drop_column("transactions", "biller_item_name")
    op.drop_column("transactions", "biller_item_code")
    op.drop_column("transactions", "biller_code")
    op.drop_column("transactions", "mobile_network")
    op.drop_column("transactions", "target_phone_number")
