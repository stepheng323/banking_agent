"""add_scheduled_transactions_tables

Revision ID: 8a69f2dc1b2f
Revises: 276932c56c73
Create Date: 2026-03-05 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8a69f2dc1b2f"
down_revision: str | None = "276932c56c73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_instructions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("payload_snapshot", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("recurrence_type", sa.String(), nullable=False),
        sa.Column("start_date", sa.String(), nullable=False),
        sa.Column("local_time", sa.String(), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("end_date", sa.String(), nullable=True),
        sa.Column("next_run_at_utc", sa.DateTime(), nullable=False),
        sa.Column("last_run_at_utc", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("channel_identity", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_scheduled_instructions_user_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_scheduled_instructions_id"), "scheduled_instructions", ["id"], unique=False)
    op.create_index(
        op.f("ix_scheduled_instructions_user_id"), "scheduled_instructions", ["user_id"], unique=False
    )
    op.create_index(op.f("ix_scheduled_instructions_domain"), "scheduled_instructions", ["domain"], unique=False)
    op.create_index(op.f("ix_scheduled_instructions_status"), "scheduled_instructions", ["status"], unique=False)
    op.create_index(
        op.f("ix_scheduled_instructions_recurrence_type"),
        "scheduled_instructions",
        ["recurrence_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_instructions_next_run_at_utc"),
        "scheduled_instructions",
        ["next_run_at_utc"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_instructions_created_at"), "scheduled_instructions", ["created_at"], unique=False
    )

    op.create_table(
        "scheduled_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("due_at_utc", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["schedule_id"], ["scheduled_instructions.id"], name="fk_scheduled_runs_schedule_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_scheduled_runs_id"), "scheduled_runs", ["id"], unique=False)
    op.create_index(op.f("ix_scheduled_runs_schedule_id"), "scheduled_runs", ["schedule_id"], unique=False)
    op.create_index(op.f("ix_scheduled_runs_due_at_utc"), "scheduled_runs", ["due_at_utc"], unique=False)
    op.create_index(op.f("ix_scheduled_runs_status"), "scheduled_runs", ["status"], unique=False)
    op.create_index(op.f("ix_scheduled_runs_idempotency_key"), "scheduled_runs", ["idempotency_key"], unique=True)
    op.create_index(op.f("ix_scheduled_runs_created_at"), "scheduled_runs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_scheduled_runs_created_at"), table_name="scheduled_runs")
    op.drop_index(op.f("ix_scheduled_runs_idempotency_key"), table_name="scheduled_runs")
    op.drop_index(op.f("ix_scheduled_runs_status"), table_name="scheduled_runs")
    op.drop_index(op.f("ix_scheduled_runs_due_at_utc"), table_name="scheduled_runs")
    op.drop_index(op.f("ix_scheduled_runs_schedule_id"), table_name="scheduled_runs")
    op.drop_index(op.f("ix_scheduled_runs_id"), table_name="scheduled_runs")
    op.drop_table("scheduled_runs")

    op.drop_index(op.f("ix_scheduled_instructions_created_at"), table_name="scheduled_instructions")
    op.drop_index(op.f("ix_scheduled_instructions_next_run_at_utc"), table_name="scheduled_instructions")
    op.drop_index(op.f("ix_scheduled_instructions_recurrence_type"), table_name="scheduled_instructions")
    op.drop_index(op.f("ix_scheduled_instructions_status"), table_name="scheduled_instructions")
    op.drop_index(op.f("ix_scheduled_instructions_domain"), table_name="scheduled_instructions")
    op.drop_index(op.f("ix_scheduled_instructions_user_id"), table_name="scheduled_instructions")
    op.drop_index(op.f("ix_scheduled_instructions_id"), table_name="scheduled_instructions")
    op.drop_table("scheduled_instructions")
