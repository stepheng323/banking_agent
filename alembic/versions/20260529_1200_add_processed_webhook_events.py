"""add_processed_webhook_events

Revision ID: 9d7e6f4a2b31
Revises: 4b4cb47c7c1f
Create Date: 2026-05-29 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d7e6f4a2b31"
down_revision: str | None = "4b4cb47c7c1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("event_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "event_id", name="uq_processed_webhook_events_provider_event_id"),
    )
    op.create_index(op.f("ix_processed_webhook_events_id"), "processed_webhook_events", ["id"], unique=False)
    op.create_index(
        op.f("ix_processed_webhook_events_provider"),
        "processed_webhook_events",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_processed_webhook_events_event_name"),
        "processed_webhook_events",
        ["event_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_processed_webhook_events_status"),
        "processed_webhook_events",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_processed_webhook_events_first_seen_at"),
        "processed_webhook_events",
        ["first_seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_processed_webhook_events_first_seen_at"), table_name="processed_webhook_events")
    op.drop_index(op.f("ix_processed_webhook_events_status"), table_name="processed_webhook_events")
    op.drop_index(op.f("ix_processed_webhook_events_event_name"), table_name="processed_webhook_events")
    op.drop_index(op.f("ix_processed_webhook_events_provider"), table_name="processed_webhook_events")
    op.drop_index(op.f("ix_processed_webhook_events_id"), table_name="processed_webhook_events")
    op.drop_table("processed_webhook_events")
