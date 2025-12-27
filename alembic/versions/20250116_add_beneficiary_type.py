"""add_beneficiary_type

Revision ID: add_beneficiary_type_001
Revises: 940b5c5ee7e8
Create Date: 2025-01-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_beneficiary_type_001"
down_revision: str | None = "940b5c5ee7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add beneficiary_type column with default 'transfer' for existing records
    op.add_column(
        "beneficiaries",
        sa.Column("beneficiary_type", sa.String(), nullable=False, server_default="transfer"),
    )
    # Create index for better query performance
    op.create_index("ix_beneficiaries_beneficiary_type", "beneficiaries", ["beneficiary_type"])


def downgrade() -> None:
    op.drop_index("ix_beneficiaries_beneficiary_type", table_name="beneficiaries")
    op.drop_column("beneficiaries", "beneficiary_type")
