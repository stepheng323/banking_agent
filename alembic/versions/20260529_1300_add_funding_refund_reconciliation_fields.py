"""add_funding_refund_reconciliation_fields

Revision ID: b4a9c6d1e2f3
Revises: 9d7e6f4a2b31
Create Date: 2026-05-29 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4a9c6d1e2f3"
down_revision: str | None = "9d7e6f4a2b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("funding_steps", sa.Column("refund_provider_id", sa.String(), nullable=True))
    op.add_column("funding_steps", sa.Column("refund_provider_reference", sa.String(), nullable=True))
    op.add_column("funding_steps", sa.Column("refund_initiated_at", sa.DateTime(), nullable=True))
    op.add_column("funding_steps", sa.Column("refund_last_checked_at", sa.DateTime(), nullable=True))
    op.add_column(
        "funding_steps",
        sa.Column("refund_attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("funding_steps", sa.Column("refund_error_message", sa.String(), nullable=True))

    op.create_index(
        op.f("ix_funding_steps_refund_provider_id"),
        "funding_steps",
        ["refund_provider_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_funding_steps_refund_provider_reference"),
        "funding_steps",
        ["refund_provider_reference"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_funding_steps_refund_provider_reference"), table_name="funding_steps")
    op.drop_index(op.f("ix_funding_steps_refund_provider_id"), table_name="funding_steps")
    op.drop_column("funding_steps", "refund_error_message")
    op.drop_column("funding_steps", "refund_attempt_count")
    op.drop_column("funding_steps", "refund_last_checked_at")
    op.drop_column("funding_steps", "refund_initiated_at")
    op.drop_column("funding_steps", "refund_provider_reference")
    op.drop_column("funding_steps", "refund_provider_id")
