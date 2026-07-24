"""add query semantic enrichment

Revision ID: d8e9f0a1b2c3
Revises: c7e8a9b0d1f2
Create Date: 2026-07-21 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: str | None = "c7e8a9b0d1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_TYPE = postgresql.UUID(as_uuid=True)
MONEY_TYPE = sa.Numeric(18, 2)


def upgrade() -> None:
    op.create_table(
        "query_transactions",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column(
            "user_id", UUID_TYPE, sa.ForeignKey("users.id", name="fk_query_transactions_user_id"), nullable=False
        ),
        sa.Column(
            "linked_account_id", UUID_TYPE, sa.ForeignKey("accounts.id", name="fk_query_transactions_linked_account_id")
        ),
        sa.Column("effective_at", sa.DateTime(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("amount", MONEY_TYPE, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="NGN"),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="posted"),
        sa.Column("narration", sa.Text()),
        sa.Column("narration_fingerprint", sa.String(length=128)),
        sa.Column("bank_name", sa.String()),
        sa.Column("source_reconciliation_status", sa.String(length=20), nullable=False, server_default="unmatched"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("direction in ('debit', 'credit')", name="ck_query_transactions_direction"),
        sa.CheckConstraint("amount >= 0", name="ck_query_transactions_amount_nonnegative"),
    )
    for name, columns in (
        ("ix_query_transactions_user_id", ["user_id"]),
        ("ix_query_transactions_linked_account_id", ["linked_account_id"]),
        ("ix_query_transactions_effective_at", ["effective_at"]),
        ("ix_query_transactions_effective_date", ["effective_date"]),
        ("ix_query_transactions_direction", ["direction"]),
        ("ix_query_transactions_status", ["status"]),
        ("ix_query_transactions_narration_fingerprint", ["narration_fingerprint"]),
        ("ix_query_transactions_user_effective", ["user_id", "effective_at"]),
    ):
        op.create_index(name, "query_transactions", columns)

    op.create_table(
        "query_transaction_sources",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column(
            "query_transaction_id",
            UUID_TYPE,
            sa.ForeignKey(
                "query_transactions.id", name="fk_query_transaction_sources_query_transaction_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=False),
        sa.Column("provider", sa.String(length=60)),
        sa.Column("provider_reference", sa.String(length=160)),
        sa.Column("match_confidence", sa.String(length=20), nullable=False, server_default="exact"),
        sa.Column("observed_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source_kind", "source_id", name="uq_query_transaction_sources_source"),
    )
    for name, columns in (
        ("ix_query_transaction_sources_query_transaction_id", ["query_transaction_id"]),
        ("ix_query_transaction_sources_source_kind", ["source_kind"]),
        ("ix_query_transaction_sources_provider", ["provider"]),
        ("ix_query_transaction_sources_provider_reference", ["provider_reference"]),
    ):
        op.create_index(name, "query_transaction_sources", columns)

    op.create_table(
        "counterparty_entities",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("owner_user_id", UUID_TYPE, sa.ForeignKey("users.id", name="fk_counterparty_entities_owner_user_id")),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False, server_default="unknown"),
        sa.Column("verification_status", sa.String(length=20), nullable=False, server_default="unverified"),
        sa.Column("default_category", sa.String(length=80)),
        sa.Column("supported_event_types", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("owner_user_id", "canonical_name", name="uq_counterparty_entities_owner_name"),
    )
    for name, columns in (
        ("ix_counterparty_entities_owner_user_id", ["owner_user_id"]),
        ("ix_counterparty_entities_canonical_name", ["canonical_name"]),
        ("ix_counterparty_entities_entity_type", ["entity_type"]),
        ("ix_counterparty_entities_verification_status", ["verification_status"]),
    ):
        op.create_index(name, "counterparty_entities", columns)

    op.create_table(
        "counterparty_aliases",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column(
            "entity_id",
            UUID_TYPE,
            sa.ForeignKey("counterparty_entities.id", name="fk_counterparty_aliases_entity_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias_normalized", sa.String(length=240), nullable=False),
        sa.Column("alias_kind", sa.String(length=32), nullable=False, server_default="name"),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False, server_default="1"),
        sa.Column("verification_status", sa.String(length=20), nullable=False, server_default="unverified"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("entity_id", "alias_normalized", "alias_kind", name="uq_counterparty_aliases_entity_alias"),
    )
    op.create_index("ix_counterparty_aliases_entity_id", "counterparty_aliases", ["entity_id"])
    op.create_index("ix_counterparty_aliases_alias_normalized", "counterparty_aliases", ["alias_normalized"])

    op.create_table(
        "counterparty_identifiers",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column(
            "entity_id",
            UUID_TYPE,
            sa.ForeignKey("counterparty_entities.id", name="fk_counterparty_identifiers_entity_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identifier_type", sa.String(length=40), nullable=False),
        sa.Column("value_hash", sa.String(length=128), nullable=False),
        sa.Column("encrypted_value", sa.Text()),
        sa.Column("verification_status", sa.String(length=20), nullable=False, server_default="unverified"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("identifier_type", "value_hash", name="uq_counterparty_identifiers_type_hash"),
    )
    op.create_index("ix_counterparty_identifiers_entity_id", "counterparty_identifiers", ["entity_id"])
    op.create_index("ix_counterparty_identifiers_value_hash", "counterparty_identifiers", ["value_hash"])

    op.create_table(
        "user_counterparty_profiles",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            UUID_TYPE,
            sa.ForeignKey("users.id", name="fk_user_counterparty_profiles_user_id"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            UUID_TYPE,
            sa.ForeignKey("counterparty_entities.id", name="fk_user_counterparty_profiles_entity_id"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(length=40)),
        sa.Column("preferred_name", sa.String(length=200)),
        sa.Column("category_override", sa.String(length=80)),
        sa.Column("verified_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "entity_id", name="uq_user_counterparty_profiles_user_entity"),
    )
    op.create_index("ix_user_counterparty_profiles_user_id", "user_counterparty_profiles", ["user_id"])
    op.create_index("ix_user_counterparty_profiles_entity_id", "user_counterparty_profiles", ["entity_id"])

    op.create_table(
        "enrichment_runs",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("enrichment_version", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("model_version", sa.String(length=120)),
        sa.Column("scanned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index("ix_enrichment_runs_enrichment_version", "enrichment_runs", ["enrichment_version"])
    op.create_index("ix_enrichment_runs_source", "enrichment_runs", ["source"])
    op.create_index("ix_enrichment_runs_status", "enrichment_runs", ["status"])

    op.create_table(
        "transaction_semantic_assertions",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column(
            "query_transaction_id",
            UUID_TYPE,
            sa.ForeignKey(
                "query_transactions.id", name="fk_semantic_assertions_query_transaction_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "enrichment_run_id", UUID_TYPE, sa.ForeignKey("enrichment_runs.id", name="fk_semantic_assertions_run_id")
        ),
        sa.Column("field_name", sa.String(length=48), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("resolution_state", sa.String(length=20), nullable=False, server_default="resolved"),
        sa.Column("rule_id", sa.String(length=120)),
        sa.Column("model_version", sa.String(length=120)),
        sa.Column("enrichment_version", sa.String(length=80), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("superseded_at", sa.DateTime()),
    )
    for name, columns in (
        ("ix_semantic_assertions_query_transaction_id", ["query_transaction_id"]),
        ("ix_semantic_assertions_enrichment_run_id", ["enrichment_run_id"]),
        ("ix_semantic_assertions_field_name", ["field_name"]),
        ("ix_semantic_assertions_source", ["source"]),
        ("ix_semantic_assertions_active", ["query_transaction_id", "field_name", "superseded_at"]),
    ):
        op.create_index(name, "transaction_semantic_assertions", columns)

    op.create_table(
        "transaction_semantic_projections",
        sa.Column(
            "query_transaction_id",
            UUID_TYPE,
            sa.ForeignKey(
                "query_transactions.id", name="fk_semantic_projections_query_transaction_id", ondelete="CASCADE"
            ),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "counterparty_entity_id",
            UUID_TYPE,
            sa.ForeignKey("counterparty_entities.id", name="fk_semantic_projections_entity_id"),
        ),
        sa.Column("counterparty_name", sa.String(length=200)),
        sa.Column("entity_type", sa.String(length=40)),
        sa.Column("event_type", sa.String(length=48)),
        sa.Column("category", sa.String(length=80)),
        sa.Column("cash_flow_class", sa.String(length=32)),
        sa.Column("resolution_state", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("enrichment_version", sa.String(length=80), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    for name, columns in (
        ("ix_semantic_projections_counterparty_entity_id", ["counterparty_entity_id"]),
        ("ix_semantic_projections_counterparty_name", ["counterparty_name"]),
        ("ix_semantic_projections_entity_type", ["entity_type"]),
        ("ix_semantic_projections_event_type", ["event_type"]),
        ("ix_semantic_projections_category", ["category"]),
        ("ix_semantic_projections_cash_flow_class", ["cash_flow_class"]),
        ("ix_semantic_projections_resolution_state", ["resolution_state"]),
    ):
        op.create_index(name, "transaction_semantic_projections", columns)

    op.create_table(
        "transaction_relationships",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column(
            "source_transaction_id",
            UUID_TYPE,
            sa.ForeignKey("query_transactions.id", name="fk_transaction_relationships_source"),
            nullable=False,
        ),
        sa.Column(
            "target_transaction_id",
            UUID_TYPE,
            sa.ForeignKey("query_transactions.id", name="fk_transaction_relationships_target"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="candidate"),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("enrichment_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("superseded_at", sa.DateTime()),
        sa.UniqueConstraint(
            "source_transaction_id",
            "target_transaction_id",
            "relationship_type",
            "superseded_at",
            name="uq_transaction_relationships_active",
        ),
        sa.CheckConstraint(
            "source_transaction_id <> target_transaction_id", name="ck_transaction_relationships_not_self"
        ),
    )
    for name, columns in (
        ("ix_transaction_relationships_source", ["source_transaction_id"]),
        ("ix_transaction_relationships_target", ["target_transaction_id"]),
        ("ix_transaction_relationships_type", ["relationship_type"]),
        ("ix_transaction_relationships_status", ["status"]),
    ):
        op.create_index(name, "transaction_relationships", columns)

    op.create_table(
        "economic_events",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("user_id", UUID_TYPE, sa.ForeignKey("users.id", name="fk_economic_events_user_id"), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("economic_amount", MONEY_TYPE, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="NGN"),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column(
            "counterparty_entity_id",
            UUID_TYPE,
            sa.ForeignKey("counterparty_entities.id", name="fk_economic_events_entity_id"),
        ),
        sa.Column("category", sa.String(length=80)),
        sa.Column("cash_flow_class", sa.String(length=32)),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="provisional"),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("effective_at", sa.DateTime(), nullable=False),
        sa.Column("enrichment_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    for name, columns in (
        ("ix_economic_events_user_id", ["user_id"]),
        ("ix_economic_events_event_type", ["event_type"]),
        ("ix_economic_events_direction", ["direction"]),
        ("ix_economic_events_entity", ["counterparty_entity_id"]),
        ("ix_economic_events_category", ["category"]),
        ("ix_economic_events_cash_flow_class", ["cash_flow_class"]),
        ("ix_economic_events_status", ["status"]),
        ("ix_economic_events_effective_at", ["effective_at"]),
    ):
        op.create_index(name, "economic_events", columns)

    op.create_table(
        "economic_event_transactions",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column(
            "economic_event_id",
            UUID_TYPE,
            sa.ForeignKey("economic_events.id", name="fk_economic_event_transactions_event_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "query_transaction_id",
            UUID_TYPE,
            sa.ForeignKey("query_transactions.id", name="fk_economic_event_transactions_transaction_id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("allocated_amount", MONEY_TYPE, nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.UniqueConstraint(
            "economic_event_id", "query_transaction_id", "role", name="uq_economic_event_transaction_leg"
        ),
        sa.CheckConstraint("allocated_amount >= 0", name="ck_economic_event_transactions_amount_nonnegative"),
    )
    for name, columns in (
        ("ix_economic_event_transactions_event", ["economic_event_id"]),
        ("ix_economic_event_transactions_transaction", ["query_transaction_id"]),
        ("ix_economic_event_transactions_role", ["role"]),
    ):
        op.create_index(name, "economic_event_transactions", columns)

    op.create_table(
        "balance_snapshots",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("user_id", UUID_TYPE, sa.ForeignKey("users.id", name="fk_balance_snapshots_user_id"), nullable=False),
        sa.Column(
            "linked_account_id",
            UUID_TYPE,
            sa.ForeignKey("accounts.id", name="fk_balance_snapshots_account_id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=60), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("ledger_effective_at", sa.DateTime()),
        sa.Column("available_balance", MONEY_TYPE),
        sa.Column("ledger_balance", MONEY_TYPE),
        sa.Column("coverage_status", sa.String(length=20), nullable=False, server_default="unavailable"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    for name, columns in (
        ("ix_balance_snapshots_user", ["user_id"]),
        ("ix_balance_snapshots_account", ["linked_account_id"]),
        ("ix_balance_snapshots_provider", ["provider"]),
        ("ix_balance_snapshots_captured", ["captured_at"]),
    ):
        op.create_index(name, "balance_snapshots", columns)

    op.create_table(
        "semantic_reconciliation_runs",
        sa.Column("id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("user_id", UUID_TYPE, sa.ForeignKey("users.id", name="fk_semantic_reconciliation_runs_user_id")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("enrichment_version", sa.String(length=80), nullable=False),
        sa.Column("scanned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assigned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unresolved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unresolved_amount", MONEY_TYPE, nullable=False, server_default="0"),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_index("ix_semantic_reconciliation_runs_user", "semantic_reconciliation_runs", ["user_id"])
    op.create_index("ix_semantic_reconciliation_runs_status", "semantic_reconciliation_runs", ["status"])


def downgrade() -> None:
    for table in (
        "semantic_reconciliation_runs",
        "balance_snapshots",
        "economic_event_transactions",
        "economic_events",
        "transaction_relationships",
        "transaction_semantic_projections",
        "transaction_semantic_assertions",
        "enrichment_runs",
        "user_counterparty_profiles",
        "counterparty_identifiers",
        "counterparty_aliases",
        "counterparty_entities",
        "query_transaction_sources",
        "query_transactions",
    ):
        op.drop_table(table)
