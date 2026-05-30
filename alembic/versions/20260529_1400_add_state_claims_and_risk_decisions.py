"""add_state_claims_and_risk_decisions

Revision ID: c8f3a7b2d9e4
Revises: b4a9c6d1e2f3
Create Date: 2026-05-29 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8f3a7b2d9e4"
down_revision: str | None = "b4a9c6d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("risk_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_risk_decisions_user_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_risk_decisions_idempotency_key"),
    )
    op.create_index(op.f("ix_risk_decisions_id"), "risk_decisions", ["id"], unique=False)
    op.create_index(op.f("ix_risk_decisions_user_id"), "risk_decisions", ["user_id"], unique=False)
    op.create_index(op.f("ix_risk_decisions_idempotency_key"), "risk_decisions", ["idempotency_key"], unique=False)
    op.create_index(op.f("ix_risk_decisions_decision"), "risk_decisions", ["decision"], unique=False)
    op.create_index(op.f("ix_risk_decisions_status"), "risk_decisions", ["status"], unique=False)
    op.create_index(op.f("ix_risk_decisions_created_at"), "risk_decisions", ["created_at"], unique=False)

    op.create_unique_constraint(
        "uq_funding_steps_transfer_sequence",
        "funding_steps",
        ["funded_transfer_id", "sequence"],
    )
    op.create_unique_constraint(
        "uq_funding_steps_provider_debit_id",
        "funding_steps",
        ["provider_debit_id"],
    )
    op.create_unique_constraint(
        "uq_funded_transfers_payout_reference",
        "funded_transfers",
        ["payout_reference"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_funded_transfers_payout_reference", "funded_transfers", type_="unique")
    op.drop_constraint("uq_funding_steps_provider_debit_id", "funding_steps", type_="unique")
    op.drop_constraint("uq_funding_steps_transfer_sequence", "funding_steps", type_="unique")
    op.drop_index(op.f("ix_risk_decisions_created_at"), table_name="risk_decisions")
    op.drop_index(op.f("ix_risk_decisions_status"), table_name="risk_decisions")
    op.drop_index(op.f("ix_risk_decisions_decision"), table_name="risk_decisions")
    op.drop_index(op.f("ix_risk_decisions_idempotency_key"), table_name="risk_decisions")
    op.drop_index(op.f("ix_risk_decisions_user_id"), table_name="risk_decisions")
    op.drop_index(op.f("ix_risk_decisions_id"), table_name="risk_decisions")
    op.drop_table("risk_decisions")
