"""Reset query-side demo data before reseeding semantic transaction examples.

This intentionally preserves operational transactions and the immutable ledger.
Run only against a non-production/demo database:

    PYTHONPATH=. uv run python -m scripts.reset_query_semantic_data --phone 2348000000000 --yes
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import delete, select

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.database.connection import get_session_local
from shared.database.models import (
    Account,
    BalanceSnapshot,
    BankTransaction,
    BankTransactionCoverage,
    EconomicEvent,
    EconomicEventTransaction,
    QueryTransaction,
    QueryTransactionSource,
    SemanticReconciliationRun,
    TransactionRelationship,
    TransactionSemanticAssertion,
    TransactionSemanticProjection,
    User,
)


async def reset_query_semantic_data(*, phone: str) -> None:
    session_local = get_session_local()
    async with session_local() as db:
        user = (await db.execute(select(User).where(User.phone_number == phone))).scalars().first()
        if user is None:
            raise ValueError(f"No user found for phone {phone}")

        query_ids = list(
            (await db.execute(select(QueryTransaction.id).where(QueryTransaction.user_id == user.id))).scalars()
        )
        event_ids = list((await db.execute(select(EconomicEvent.id).where(EconomicEvent.user_id == user.id))).scalars())
        account_ids = list((await db.execute(select(Account.id).where(Account.user_id == user.id))).scalars())
        if event_ids:
            await db.execute(
                delete(EconomicEventTransaction).where(EconomicEventTransaction.economic_event_id.in_(event_ids))
            )
        if query_ids:
            await db.execute(
                delete(TransactionRelationship).where(TransactionRelationship.source_transaction_id.in_(query_ids))
            )
            await db.execute(
                delete(TransactionRelationship).where(TransactionRelationship.target_transaction_id.in_(query_ids))
            )
            await db.execute(
                delete(TransactionSemanticAssertion).where(
                    TransactionSemanticAssertion.query_transaction_id.in_(query_ids)
                )
            )
            await db.execute(
                delete(TransactionSemanticProjection).where(
                    TransactionSemanticProjection.query_transaction_id.in_(query_ids)
                )
            )
            await db.execute(
                delete(QueryTransactionSource).where(QueryTransactionSource.query_transaction_id.in_(query_ids))
            )
        await db.execute(delete(EconomicEvent).where(EconomicEvent.user_id == user.id))
        await db.execute(delete(QueryTransaction).where(QueryTransaction.user_id == user.id))
        await db.execute(delete(BalanceSnapshot).where(BalanceSnapshot.user_id == user.id))
        await db.execute(delete(SemanticReconciliationRun).where(SemanticReconciliationRun.user_id == user.id))
        if account_ids:
            await db.execute(
                delete(BankTransactionCoverage).where(BankTransactionCoverage.linked_account_id.in_(account_ids))
            )
        await db.execute(delete(BankTransaction).where(BankTransaction.user_id == user.id))
        await db.commit()

    try:
        await UserDataCache(RedisClient.get_client()).invalidate_all_user_data(phone)
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset query-side semantic demo data for one user.")
    parser.add_argument("--phone", required=True)
    parser.add_argument("--yes", action="store_true", help="Required acknowledgement for destructive demo-data reset.")
    args = parser.parse_args()
    if not args.yes:
        parser.error("Pass --yes to reset query-side demo data.")
    asyncio.run(reset_query_semantic_data(phone=args.phone))


if __name__ == "__main__":
    main()
