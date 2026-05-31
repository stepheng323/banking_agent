"""add_immutable_ledger_tables

Revision ID: a2c4d6e8f0b1
Revises: f1a2b3c4d5e6
Create Date: 2026-05-30 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2c4d6e8f0b1"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_TYPE = postgresql.UUID(as_uuid=True)
MONEY_TYPE = sa.Numeric(18, 2)


def _create_immutability_triggers() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION prevent_ledger_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'ledger entries and lines are immutable';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        for table_name in ("ledger_entries", "ledger_lines"):
            op.execute(
                f"""
                CREATE TRIGGER prevent_{table_name}_update
                BEFORE UPDATE ON {table_name}
                FOR EACH ROW EXECUTE FUNCTION prevent_ledger_mutation();
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER prevent_{table_name}_delete
                BEFORE DELETE ON {table_name}
                FOR EACH ROW EXECUTE FUNCTION prevent_ledger_mutation();
                """
            )
        return

    if dialect == "sqlite":
        for table_name in ("ledger_entries", "ledger_lines"):
            op.execute(
                f"""
                CREATE TRIGGER prevent_{table_name}_update
                BEFORE UPDATE ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, '{table_name} rows are immutable');
                END;
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER prevent_{table_name}_delete
                BEFORE DELETE ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, '{table_name} rows are immutable');
                END;
                """
            )


def _drop_immutability_triggers() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        for table_name in ("ledger_entries", "ledger_lines"):
            op.execute(f"DROP TRIGGER IF EXISTS prevent_{table_name}_update ON {table_name}")
            op.execute(f"DROP TRIGGER IF EXISTS prevent_{table_name}_delete ON {table_name}")
        op.execute("DROP FUNCTION IF EXISTS prevent_ledger_mutation()")
        return

    if dialect == "sqlite":
        for table_name in ("ledger_entries", "ledger_lines"):
            op.execute(f"DROP TRIGGER IF EXISTS prevent_{table_name}_update")
            op.execute(f"DROP TRIGGER IF EXISTS prevent_{table_name}_delete")


def upgrade() -> None:
    op.create_table(
        "ledger_accounts",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("code", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("account_type", sa.String(length=40), nullable=False),
        sa.Column("normal_balance", sa.String(length=10), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="NGN"),
        sa.Column("owner_type", sa.String(length=60), nullable=True),
        sa.Column("owner_id", UUID_TYPE, nullable=True),
        sa.Column("provider", sa.String(length=60), nullable=True),
        sa.Column("user_id", UUID_TYPE, nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("normal_balance in ('debit', 'credit')", name="ck_ledger_accounts_normal_balance"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_ledger_accounts_user_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_ledger_accounts_code"),
    )
    op.create_index(op.f("ix_ledger_accounts_id"), "ledger_accounts", ["id"], unique=False)
    op.create_index(op.f("ix_ledger_accounts_code"), "ledger_accounts", ["code"], unique=True)
    op.create_index(op.f("ix_ledger_accounts_account_type"), "ledger_accounts", ["account_type"], unique=False)
    op.create_index(op.f("ix_ledger_accounts_currency"), "ledger_accounts", ["currency"], unique=False)
    op.create_index(op.f("ix_ledger_accounts_owner_type"), "ledger_accounts", ["owner_type"], unique=False)
    op.create_index(op.f("ix_ledger_accounts_owner_id"), "ledger_accounts", ["owner_id"], unique=False)
    op.create_index(op.f("ix_ledger_accounts_provider"), "ledger_accounts", ["provider"], unique=False)
    op.create_index(op.f("ix_ledger_accounts_user_id"), "ledger_accounts", ["user_id"], unique=False)
    op.create_index(op.f("ix_ledger_accounts_status"), "ledger_accounts", ["status"], unique=False)
    op.create_index(op.f("ix_ledger_accounts_created_at"), "ledger_accounts", ["created_at"], unique=False)

    op.create_table(
        "ledger_entries",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("entry_key", sa.String(length=180), nullable=False),
        sa.Column("entry_type", sa.String(length=80), nullable=False),
        sa.Column("amount_naira", MONEY_TYPE, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="NGN"),
        sa.Column("transaction_id", UUID_TYPE, nullable=True),
        sa.Column("funded_transfer_id", UUID_TYPE, nullable=True),
        sa.Column("funding_step_id", UUID_TYPE, nullable=True),
        sa.Column("provider", sa.String(length=60), nullable=True),
        sa.Column("provider_reference", sa.String(length=160), nullable=True),
        sa.Column("provider_event_id", sa.String(length=160), nullable=True),
        sa.Column("source_type", sa.String(length=60), nullable=True),
        sa.Column("source_id", sa.String(length=160), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("reversal_of_entry_id", UUID_TYPE, nullable=True),
        sa.CheckConstraint("amount_naira > 0", name="ck_ledger_entries_positive_amount"),
        sa.ForeignKeyConstraint(
            ["funded_transfer_id"],
            ["funded_transfers.id"],
            name="fk_ledger_entries_funded_transfer_id",
        ),
        sa.ForeignKeyConstraint(["funding_step_id"], ["funding_steps.id"], name="fk_ledger_entries_funding_step_id"),
        sa.ForeignKeyConstraint(
            ["reversal_of_entry_id"],
            ["ledger_entries.id"],
            name="fk_ledger_entries_reversal_of_entry_id",
        ),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], name="fk_ledger_entries_transaction_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_key", name="uq_ledger_entries_entry_key"),
    )
    op.create_index(op.f("ix_ledger_entries_id"), "ledger_entries", ["id"], unique=False)
    op.create_index(op.f("ix_ledger_entries_entry_key"), "ledger_entries", ["entry_key"], unique=True)
    op.create_index(op.f("ix_ledger_entries_entry_type"), "ledger_entries", ["entry_type"], unique=False)
    op.create_index(op.f("ix_ledger_entries_currency"), "ledger_entries", ["currency"], unique=False)
    op.create_index(op.f("ix_ledger_entries_transaction_id"), "ledger_entries", ["transaction_id"], unique=False)
    op.create_index(
        op.f("ix_ledger_entries_funded_transfer_id"),
        "ledger_entries",
        ["funded_transfer_id"],
        unique=False,
    )
    op.create_index(op.f("ix_ledger_entries_funding_step_id"), "ledger_entries", ["funding_step_id"], unique=False)
    op.create_index(op.f("ix_ledger_entries_provider"), "ledger_entries", ["provider"], unique=False)
    op.create_index(
        op.f("ix_ledger_entries_provider_reference"),
        "ledger_entries",
        ["provider_reference"],
        unique=False,
    )
    op.create_index(op.f("ix_ledger_entries_provider_event_id"), "ledger_entries", ["provider_event_id"], unique=False)
    op.create_index(op.f("ix_ledger_entries_source_type"), "ledger_entries", ["source_type"], unique=False)
    op.create_index(op.f("ix_ledger_entries_source_id"), "ledger_entries", ["source_id"], unique=False)
    op.create_index(op.f("ix_ledger_entries_posted_at"), "ledger_entries", ["posted_at"], unique=False)
    op.create_index(op.f("ix_ledger_entries_created_at"), "ledger_entries", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_ledger_entries_reversal_of_entry_id"),
        "ledger_entries",
        ["reversal_of_entry_id"],
        unique=False,
    )

    op.create_table(
        "ledger_lines",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("entry_id", UUID_TYPE, nullable=False),
        sa.Column("account_id", UUID_TYPE, nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("amount_naira", MONEY_TYPE, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="NGN"),
        sa.Column("transaction_id", UUID_TYPE, nullable=True),
        sa.Column("funded_transfer_id", UUID_TYPE, nullable=True),
        sa.Column("funding_step_id", UUID_TYPE, nullable=True),
        sa.Column("provider", sa.String(length=60), nullable=True),
        sa.Column("provider_reference", sa.String(length=160), nullable=True),
        sa.Column("provider_event_id", sa.String(length=160), nullable=True),
        sa.Column("posted_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_naira > 0", name="ck_ledger_lines_positive_amount"),
        sa.CheckConstraint("direction in ('debit', 'credit')", name="ck_ledger_lines_direction"),
        sa.ForeignKeyConstraint(["account_id"], ["ledger_accounts.id"], name="fk_ledger_lines_account_id"),
        sa.ForeignKeyConstraint(["entry_id"], ["ledger_entries.id"], name="fk_ledger_lines_entry_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "line_number", name="uq_ledger_lines_entry_line_number"),
    )
    op.create_index(op.f("ix_ledger_lines_id"), "ledger_lines", ["id"], unique=False)
    op.create_index(op.f("ix_ledger_lines_entry_id"), "ledger_lines", ["entry_id"], unique=False)
    op.create_index(op.f("ix_ledger_lines_account_id"), "ledger_lines", ["account_id"], unique=False)
    op.create_index(op.f("ix_ledger_lines_direction"), "ledger_lines", ["direction"], unique=False)
    op.create_index(op.f("ix_ledger_lines_currency"), "ledger_lines", ["currency"], unique=False)
    op.create_index(op.f("ix_ledger_lines_transaction_id"), "ledger_lines", ["transaction_id"], unique=False)
    op.create_index(op.f("ix_ledger_lines_funded_transfer_id"), "ledger_lines", ["funded_transfer_id"], unique=False)
    op.create_index(op.f("ix_ledger_lines_funding_step_id"), "ledger_lines", ["funding_step_id"], unique=False)
    op.create_index(op.f("ix_ledger_lines_provider"), "ledger_lines", ["provider"], unique=False)
    op.create_index(op.f("ix_ledger_lines_provider_reference"), "ledger_lines", ["provider_reference"], unique=False)
    op.create_index(op.f("ix_ledger_lines_provider_event_id"), "ledger_lines", ["provider_event_id"], unique=False)
    op.create_index(op.f("ix_ledger_lines_posted_at"), "ledger_lines", ["posted_at"], unique=False)
    op.create_index(op.f("ix_ledger_lines_created_at"), "ledger_lines", ["created_at"], unique=False)
    op.create_index("ix_ledger_lines_account_posted_at", "ledger_lines", ["account_id", "posted_at"], unique=False)

    op.create_table(
        "ledger_reconciliation_runs",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("run_type", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("scanned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repaired_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("finding_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ledger_reconciliation_runs_id"), "ledger_reconciliation_runs", ["id"], unique=False)
    op.create_index(
        op.f("ix_ledger_reconciliation_runs_run_type"),
        "ledger_reconciliation_runs",
        ["run_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_runs_status"),
        "ledger_reconciliation_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_runs_started_at"),
        "ledger_reconciliation_runs",
        ["started_at"],
        unique=False,
    )

    op.create_table(
        "ledger_reconciliation_findings",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("finding_key", sa.String(length=220), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("finding_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("transaction_id", UUID_TYPE, nullable=True),
        sa.Column("funded_transfer_id", UUID_TYPE, nullable=True),
        sa.Column("funding_step_id", UUID_TYPE, nullable=True),
        sa.Column("support_ticket_id", UUID_TYPE, nullable=True),
        sa.Column("expected_amount_naira", MONEY_TYPE, nullable=True),
        sa.Column("actual_amount_naira", MONEY_TYPE, nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["funded_transfer_id"],
            ["funded_transfers.id"],
            name="fk_ledger_findings_funded_transfer_id",
        ),
        sa.ForeignKeyConstraint(["funding_step_id"], ["funding_steps.id"], name="fk_ledger_findings_funding_step_id"),
        sa.ForeignKeyConstraint(
            ["support_ticket_id"],
            ["support_tickets.id"],
            name="fk_ledger_findings_support_ticket_id",
        ),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], name="fk_ledger_findings_transaction_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("finding_key", name="uq_ledger_findings_finding_key"),
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_id"),
        "ledger_reconciliation_findings",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_finding_key"),
        "ledger_reconciliation_findings",
        ["finding_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_severity"),
        "ledger_reconciliation_findings",
        ["severity"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_finding_type"),
        "ledger_reconciliation_findings",
        ["finding_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_status"),
        "ledger_reconciliation_findings",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_transaction_id"),
        "ledger_reconciliation_findings",
        ["transaction_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_funded_transfer_id"),
        "ledger_reconciliation_findings",
        ["funded_transfer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_funding_step_id"),
        "ledger_reconciliation_findings",
        ["funding_step_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_support_ticket_id"),
        "ledger_reconciliation_findings",
        ["support_ticket_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_first_seen_at"),
        "ledger_reconciliation_findings",
        ["first_seen_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_reconciliation_findings_last_seen_at"),
        "ledger_reconciliation_findings",
        ["last_seen_at"],
        unique=False,
    )

    _create_immutability_triggers()


def downgrade() -> None:
    _drop_immutability_triggers()

    op.drop_table("ledger_reconciliation_findings")
    op.drop_table("ledger_reconciliation_runs")
    op.drop_index("ix_ledger_lines_account_posted_at", table_name="ledger_lines")
    op.drop_table("ledger_lines")
    op.drop_table("ledger_entries")
    op.drop_table("ledger_accounts")
