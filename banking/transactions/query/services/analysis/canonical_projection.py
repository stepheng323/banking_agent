"""Transactional ingestion helpers for the canonical query-side read model."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.transactions.query.services.analysis.economic_events import EconomicEventBuilder
from banking.transactions.query.services.analysis.semantic_enrichment import SemanticEnrichmentService
from banking.transactions.repositories.query_transaction_repository import QueryTransactionRepository
from shared.database.models import BankTransaction, Transaction


async def project_app_transaction_read_model(db: AsyncSession, transaction: Transaction) -> None:
    """Persist the app transaction source and its safe semantic projection.

    This function shares the caller's database transaction.  It deliberately
    does not call a provider or emit a message: a query-side projection must
    never affect money-movement completion.
    """
    repository = QueryTransactionRepository(db)
    query_transaction = await repository.project_app_transaction(transaction)
    enrichment = SemanticEnrichmentService(db)
    projection = await enrichment.enrich_query_transaction(
        query_transaction,
        provider_category=transaction.transaction_type,
        provider_counterparty=transaction.recipient_name or transaction.biller_item_name,
    )
    event_builder = EconomicEventBuilder(db)
    if query_transaction.status == "successful":
        await event_builder.rebuild_for_transaction(query_transaction, projection)
        await event_builder.link_high_confidence_candidates(query_transaction)
    elif query_transaction.source_reconciliation_status != "exact":
        await event_builder.remove_for_transaction(query_transaction.id)


async def rebuild_user_query_semantics(db: AsyncSession, user_id: object) -> tuple[int, int]:
    """Rebuild canonical projections from immutable source records for one user.

    Bank observations are materialized first so a later app source can attach by
    exact provider reference.  This is safe to run after a local reseed and does
    not alter either operational transaction history or the ledger.
    """
    repository = QueryTransactionRepository(db)
    enrichment = SemanticEnrichmentService(db)
    event_builder = EconomicEventBuilder(db)
    bank_rows = list(
        (await db.execute(select(BankTransaction).where(BankTransaction.user_id == user_id))).scalars().all()
    )
    app_rows = list((await db.execute(select(Transaction).where(Transaction.user_id == user_id))).scalars().all())

    for bank_row in bank_rows:
        query_transaction = await repository.project_bank_transaction(bank_row)
        raw_payload = dict(bank_row.raw_payload or {})
        projection = await enrichment.enrich_query_transaction(
            query_transaction,
            provider_category=bank_row.category,
            provider_counterparty=raw_payload.get("provider_counterparty"),
        )
        await event_builder.rebuild_for_transaction(query_transaction, projection)
        await event_builder.link_high_confidence_candidates(query_transaction)

    for app_row in app_rows:
        await project_app_transaction_read_model(db, app_row)

    return len(bank_rows), len(app_rows)
