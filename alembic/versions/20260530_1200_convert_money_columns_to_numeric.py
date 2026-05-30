"""convert_money_columns_to_numeric

Revision ID: f1a2b3c4d5e6
Revises: c8f3a7b2d9e4
Create Date: 2026-05-30 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "c8f3a7b2d9e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY_TYPE = sa.Numeric(18, 2)


def _upgrade_amount(table_name: str) -> None:
    op.alter_column(
        table_name,
        "amount",
        existing_type=sa.Float(),
        type_=MONEY_TYPE,
        existing_nullable=False,
        postgresql_using="round(amount::numeric, 2)",
    )


def _downgrade_amount(table_name: str) -> None:
    op.alter_column(
        table_name,
        "amount",
        existing_type=MONEY_TYPE,
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="amount::double precision",
    )


def upgrade() -> None:
    for table_name in ("transactions", "bank_transactions", "funded_transfers", "funding_steps"):
        _upgrade_amount(table_name)


def downgrade() -> None:
    for table_name in ("funding_steps", "funded_transfers", "bank_transactions", "transactions"):
        _downgrade_amount(table_name)
