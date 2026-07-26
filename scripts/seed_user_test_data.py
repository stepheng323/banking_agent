"""Seed linked accounts and ambiguous beneficiaries for local testing.

Usage:
    PYTHONPATH=. uv run python -m scripts.seed_user_test_data
    PYTHONPATH=. uv run python -m scripts.seed_user_test_data --phone 2348000000000
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from calendar import monthrange
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from banking.transactions.query.services.analysis.economic_events import SemanticReconciler
from banking.transactions.query.utils.timezone import lagos_today
from banking.transactions.repositories.bank_transaction_coverage_repository import BankTransactionCoverageRepository
from banking.transactions.repositories.bank_transaction_repository import BankTransactionRepository
from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.database.connection import get_session_local
from shared.database.enums import BeneficiaryTypeEnum
from shared.database.models import (
    Account,
    BankTransaction,
    BankTransactionCoverage,
    Beneficiary,
    CounterpartyAlias,
    CounterpartyEntity,
    EconomicEvent,
    QueryTransaction,
    TransactionSemanticProjection,
    User,
)
from shared.security.field_encryption import blind_index


def _seed_account_specs(user_suffix: str) -> list[dict[str, str]]:
    return [
        {
            "account_id": f"seed-{user_suffix}-acct-1",
            "bank_name": "First Bank",
            "bank_code": "011",
            "account_number": "6000000001",
            "account_name": "Primary Test Account",
            "mandate_id": f"seed-mandate-{user_suffix}-1",
        },
        {
            "account_id": f"seed-{user_suffix}-acct-2",
            "bank_name": "GTBank",
            "bank_code": "058",
            "account_number": "6000000002",
            "account_name": "Spending Test Account",
            "mandate_id": f"seed-mandate-{user_suffix}-2",
        },
        {
            "account_id": f"seed-{user_suffix}-acct-3",
            "bank_name": "Access Bank",
            "bank_code": "044",
            "account_number": "6000000003",
            "account_name": "Savings Test Account",
            "mandate_id": f"seed-mandate-{user_suffix}-3",
        },
    ]


def _seed_beneficiary_specs() -> list[dict[str, str]]:
    return [
        {
            "account_name": "Tolu Adebayo",
            "alias": "Tolu Access",
            "account_number": "2010000001",
            "bank_code": "044",
            "bank_name": "Access Bank",
        },
        {
            "account_name": "Tolu Adebayo",
            "alias": "Tolu GTB",
            "account_number": "2010000002",
            "bank_code": "058",
            "bank_name": "GTBank",
        },
        {
            "account_name": "Tolulope Adebayo",
            "alias": "Tolu First",
            "account_number": "2010000003",
            "bank_code": "011",
            "bank_name": "First Bank",
        },
        {
            "account_name": "Esther Oyebanji",
            "alias": "Mom",
            "account_number": "2010000005",
            "bank_code": "044",
            "bank_name": "Access Bank",
        },
        {
            "account_name": "Ayomide oyebanji",
            "alias": "Ayo",
            "account_number": "2010000006",
            "bank_code": "058",
            "bank_name": "GTBank",
        },
    ]


def _counterparty_registry_specs() -> list[dict[str, object]]:
    """Small verified registry used by deterministic semantic-enrichment demos."""
    return [
        {
            "canonical_name": "EaseMoni",
            "entity_type": "lender",
            "default_category": None,
            "event_types": ["loan_disbursement", "loan_repayment"],
            "aliases": ["easemoni", "easemoni opay mfb", "repay for easemoni"],
        },
        {
            "canonical_name": "Piggyvest",
            "entity_type": "investment_platform",
            "default_category": "savings",
            "event_types": ["investment_contribution", "investment_withdrawal"],
            "aliases": ["piggyvest", "piggy vest"],
        },
        {
            "canonical_name": "Uber",
            "entity_type": "merchant",
            "default_category": "transport",
            "event_types": ["purchase"],
            "aliases": ["uber", "uber trip"],
        },
    ]


def _previous_month(value: date) -> tuple[int, int]:
    if value.month == 1:
        return value.year - 1, 12
    return value.year, value.month - 1


def _month_day(year: int, month: int, preferred_day: int) -> date:
    return date(year, month, min(preferred_day, monthrange(year, month)[1]))


def _variance_demo_transaction_specs(today: date) -> list[dict[str, object]]:
    """Return deterministic current-vs-baseline observations for local insight acceptance.

    The values intentionally exercise operating income/spending, internal and
    investing/financing exclusions, an unchanged category, and an unresolved
    narration.  They are source observations; canonical projections and
    economic events are rebuilt from them below.
    """
    baseline_year, baseline_month = _previous_month(today)
    current_year, current_month = today.year, today.month

    def observation(
        period: str,
        key: str,
        *,
        day: int,
        amount: int,
        direction: str,
        narration: str,
        category: str | None,
        account_bank: str,
        counterparty: str | None = None,
    ) -> dict[str, object]:
        year, month = (current_year, current_month) if period == "current" else (baseline_year, baseline_month)
        posted_date = _month_day(year, month, day)
        return {
            "provider_transaction_id": f"variance-demo-{period}-{key}",
            "posted_at": datetime.combine(posted_date, time(hour=12), tzinfo=UTC).replace(tzinfo=None),
            "posted_date": posted_date,
            "amount": Decimal(str(amount)),
            "transaction_type": direction,
            "narration": narration,
            "category": category,
            "counterparty": counterparty,
            "account_bank": account_bank,
        }

    fixture_rows = (
        ("current", "food", 5, 60_000, "debit", "Food Village lunch", "food", "GTBank", "Food Village"),
        ("baseline", "food", 5, 20_000, "debit", "Food Village lunch", "food", "GTBank", "Food Village"),
        ("current", "transport", 8, 30_000, "debit", "UBER TRIP LAGOS", "transport", "GTBank", "Uber"),
        ("baseline", "transport", 8, 8_000, "debit", "UBER TRIP LAGOS", "transport", "GTBank", "Uber"),
        ("current", "health", 10, 7_000, "debit", "Medical Clinic", "health", "GTBank", "Medical Clinic"),
        ("baseline", "health", 10, 7_000, "debit", "Medical Clinic", "health", "GTBank", "Medical Clinic"),
        ("current", "investment", 12, 50_000, "debit", "PIGGYVEST SAVINGS", "savings", "Access Bank", "Piggyvest"),
        ("baseline", "investment", 12, 50_000, "debit", "PIGGYVEST SAVINGS", "savings", "Access Bank", "Piggyvest"),
        ("current", "internal", 14, 10_000, "debit", "INTERNAL TRANSFER TO OWN ACCOUNT", None, "GTBank", None),
        ("baseline", "internal", 14, 10_000, "debit", "INTERNAL TRANSFER TO OWN ACCOUNT", None, "GTBank", None),
        ("current", "salary", 15, 700_000, "credit", "SALARY ACME CORP", "salary", "First Bank", "Acme Corp"),
        ("baseline", "salary", 15, 600_000, "credit", "SALARY ACME CORP", "salary", "First Bank", "Acme Corp"),
        ("current", "loan", 17, 100_000, "credit", "EASEMONI LOAN DISBURSEMENT", None, "First Bank", "EaseMoni"),
        ("baseline", "loan", 17, 100_000, "credit", "EASEMONI LOAN DISBURSEMENT", None, "First Bank", "EaseMoni"),
        ("current", "unresolved", 19, 9_000, "debit", "POS 887311", None, "GTBank", None),
        ("baseline", "unresolved", 19, 9_000, "debit", "POS 887311", None, "GTBank", None),
    )
    return [
        observation(
            period,
            key,
            day=day,
            amount=amount,
            direction=direction,
            narration=narration,
            category=category,
            account_bank=account_bank,
            counterparty=counterparty,
        )
        for period, key, day, amount, direction, narration, category, account_bank, counterparty in fixture_rows
    ]


async def _seed_variance_demo_sources(db: AsyncSession, *, user: User, accounts: list[Account]) -> int:
    """Upsert the non-production source observations used by variance acceptance."""
    accounts_by_bank = {str(account.bank_name): account for account in accounts}
    rows: list[dict[str, object]] = []
    for spec in _variance_demo_transaction_specs(lagos_today()):
        account = accounts_by_bank.get(str(spec.pop("account_bank")))
        if account is None:
            raise ValueError("variance demo requires the seeded First Bank, GTBank, and Access Bank accounts")
        rows.append(
            {
                "user_id": user.id,
                "linked_account_id": account.id,
                "provider": "mono",
                "provider_transaction_id": spec["provider_transaction_id"],
                "posted_at": spec["posted_at"],
                "posted_date": spec["posted_date"],
                "amount": spec["amount"],
                "currency": "NGN",
                "transaction_type": spec["transaction_type"],
                "narration": spec["narration"],
                "category": spec["category"],
                "counterparty": spec["counterparty"],
                "counterparty_role": None,
                "counterparty_source": "provider" if spec["counterparty"] else None,
                "resolved_category": spec["category"],
                "category_source": "provider" if spec["category"] else None,
                "parser_rule": None,
                "bank_name": account.bank_name,
                "raw_payload": {"fixture": "variance_acceptance", "provider_counterparty": spec["counterparty"]},
            }
        )

    await BankTransactionRepository(db).bulk_upsert(rows)
    baseline_year, baseline_month = _previous_month(lagos_today())
    coverage_start = _month_day(baseline_year, baseline_month, 1)
    coverage_end = lagos_today()
    coverage_repository = BankTransactionCoverageRepository(db)
    for account in accounts:
        await coverage_repository.add_full_coverage(
            account.id,
            start_date=coverage_start,
            end_date=coverage_end,
            provider="mono",
        )
    return len(rows)


async def _query_semantic_report(db: AsyncSession, *, user_id: object) -> dict[str, int]:
    """Return aggregate-only local acceptance counts; never expose transaction identifiers."""
    unresolved = int(
        (
            await db.execute(
                select(func.count())
                .select_from(QueryTransaction)
                .join(TransactionSemanticProjection)
                .where(
                    QueryTransaction.user_id == user_id,
                    TransactionSemanticProjection.resolution_state != "resolved",
                )
            )
        ).scalar_one()
    )
    coverage_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(BankTransactionCoverage)
                .join(Account, Account.id == BankTransactionCoverage.linked_account_id)
                .where(Account.user_id == user_id)
            )
        ).scalar_one()
    )
    return {
        "source_transactions": int(
            (
                await db.execute(
                    select(func.count()).select_from(BankTransaction).where(BankTransaction.user_id == user_id)
                )
            ).scalar_one()
        ),
        "query_transactions": int(
            (
                await db.execute(
                    select(func.count()).select_from(QueryTransaction).where(QueryTransaction.user_id == user_id)
                )
            ).scalar_one()
        ),
        "economic_events": int(
            (
                await db.execute(
                    select(func.count()).select_from(EconomicEvent).where(EconomicEvent.user_id == user_id)
                )
            ).scalar_one()
        ),
        "coverage_windows": coverage_count,
        "unresolved_projections": unresolved,
    }


async def _seed_counterparty_registry(db: AsyncSession) -> int:
    created = 0
    for spec in _counterparty_registry_specs():
        entity = (
            (
                await db.execute(
                    select(CounterpartyEntity).where(
                        CounterpartyEntity.owner_user_id.is_(None),
                        CounterpartyEntity.canonical_name == str(spec["canonical_name"]),
                    )
                )
            )
            .scalars()
            .first()
        )
        if entity is None:
            entity = CounterpartyEntity(
                canonical_name=str(spec["canonical_name"]),
                entity_type=str(spec["entity_type"]),
                verification_status="verified",
                default_category=spec["default_category"],
                supported_event_types=list(spec["event_types"]),
            )
            db.add(entity)
            await db.flush()
            created += 1
        for alias in list(spec["aliases"]):
            normalized = " ".join(str(alias).lower().replace("-", " ").split())
            existing = (
                (
                    await db.execute(
                        select(CounterpartyAlias).where(
                            CounterpartyAlias.entity_id == entity.id,
                            CounterpartyAlias.alias_normalized == normalized,
                            CounterpartyAlias.alias_kind == "name",
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing is None:
                db.add(
                    CounterpartyAlias(
                        entity_id=entity.id,
                        alias_normalized=normalized,
                        alias_kind="name",
                        confidence=1,
                        verification_status="verified",
                    )
                )
    return created


def _beneficiary_account_lookup(account_number: str) -> str:
    lookup = blind_index(
        "beneficiaries.account_number",
        account_number,
        normalizer="account_number",
    )
    if not lookup:
        raise ValueError(f"Could not build beneficiary account lookup for {account_number!r}")
    return lookup


async def _seed_transfer_beneficiary_matches(
    db: AsyncSession,
    *,
    user_id: object,
    spec: dict[str, str],
) -> list[Beneficiary]:
    result = await db.execute(
        select(Beneficiary)
        .where(
            Beneficiary.user_id == user_id,
            Beneficiary.beneficiary_type == BeneficiaryTypeEnum.TRANSFER.value,
            Beneficiary.account_number_blind_index == _beneficiary_account_lookup(spec["account_number"]),
            Beneficiary.bank_code == spec["bank_code"],
        )
        .order_by(Beneficiary.created_at.asc(), Beneficiary.id.asc())
    )
    return list(result.scalars().all())


async def _invalidate_seeded_user_cache(phone_number: str) -> bool:
    try:
        await UserDataCache(RedisClient.get_client()).invalidate_all_user_data(phone_number)
        return True
    except Exception as exc:
        print(f"[seed] skipped user-data cache invalidation: {exc}")
        return False


async def _resolve_target_user(phone: str | None) -> User:
    session_local = get_session_local()
    async with session_local() as db:
        if phone:
            result = await db.execute(select(User).where(User.phone_number == phone))
            user = result.scalars().first()
            if not user:
                raise ValueError(f"No user found for phone {phone}")
            return user

        result = await db.execute(select(User).order_by(User.created_at.asc()))
        users = list(result.scalars().all())
        if not users:
            raise ValueError("No users found. Create/link a user first, then run this seed.")

        if len(users) > 1:
            print(f"Found {len(users)} users. Seeding the oldest user: {users[0].phone_number}")
        return users[0]


async def _seed_for_user(user: User) -> None:
    session_local = get_session_local()
    created_accounts = 0
    updated_accounts = 0
    created_beneficiaries = 0
    updated_beneficiaries = 0
    deleted_duplicate_beneficiaries = 0
    created_counterparty_entities = 0
    rebuilt_bank_sources = 0
    rebuilt_app_sources = 0
    seeded_variance_sources = 0
    semantic_report: dict[str, int] = {}
    reconciliation_status = "not_run"

    async with session_local() as db:
        user_suffix = str(user.id).split("-")[0]

        # Seed linked accounts.
        for spec in _seed_account_specs(user_suffix):
            result = await db.execute(
                select(Account).where(
                    Account.user_id == user.id,
                    Account.account_id == spec["account_id"],
                )
            )
            account = result.scalars().first()
            if not account:
                account = Account(
                    user_id=user.id,
                    account_id=spec["account_id"],
                    bank_name=spec["bank_name"],
                    bank_code=spec["bank_code"],
                    account_number=spec["account_number"],
                    account_name=spec["account_name"],
                    mandate_status="ready",
                    mandate_id=spec["mandate_id"],
                    is_default=False,
                    extra_data={},
                )
                db.add(account)
                created_accounts += 1
            else:
                account.bank_name = spec["bank_name"]
                account.bank_code = spec["bank_code"]
                account.account_number = spec["account_number"]
                account.account_name = spec["account_name"]
                account.mandate_status = "ready"
                account.mandate_id = spec["mandate_id"]
                updated_accounts += 1

        # Ensure there is a default account for the user.
        all_accounts = list(
            (await db.execute(select(Account).where(Account.user_id == user.id).order_by(Account.created_at.asc())))
            .scalars()
            .all()
        )
        if all_accounts and not any(bool(acc.is_default) for acc in all_accounts):
            all_accounts[0].is_default = True

        # Seed similar-name beneficiaries for disambiguation tests.
        for spec in _seed_beneficiary_specs():
            matches = await _seed_transfer_beneficiary_matches(db, user_id=user.id, spec=spec)
            beneficiary = matches[0] if matches else None
            for duplicate in matches[1:]:
                await db.delete(duplicate)
                deleted_duplicate_beneficiaries += 1
            if not beneficiary:
                beneficiary = Beneficiary(
                    user_id=user.id,
                    beneficiary_type=BeneficiaryTypeEnum.TRANSFER.value,
                    account_name=spec["account_name"],
                    alias=spec["alias"],
                    account_number=spec["account_number"],
                    bank_code=spec["bank_code"],
                    bank_name=spec["bank_name"],
                )
                db.add(beneficiary)
                created_beneficiaries += 1
            else:
                beneficiary.account_name = spec["account_name"]
                beneficiary.alias = spec["alias"]
                beneficiary.account_number = spec["account_number"]
                beneficiary.bank_code = spec["bank_code"]
                beneficiary.bank_name = spec["bank_name"]
                updated_beneficiaries += 1

        created_counterparty_entities = await _seed_counterparty_registry(db)

        seeded_variance_sources = await _seed_variance_demo_sources(
            db,
            user=user,
            accounts=list(
                (await db.execute(select(Account).where(Account.user_id == user.id).order_by(Account.created_at.asc())))
                .scalars()
                .all()
            ),
        )

        from banking.transactions.query.services.analysis.canonical_projection import rebuild_user_query_semantics

        rebuilt_bank_sources, rebuilt_app_sources = await rebuild_user_query_semantics(db, user.id)
        reconciliation_status = (await SemanticReconciler(db).reconcile_user(user.id)).status
        semantic_report = await _query_semantic_report(db, user_id=user.id)

        await db.commit()

        account_rows = list(
            (await db.execute(select(Account).where(Account.user_id == user.id).order_by(Account.created_at.asc())))
            .scalars()
            .all()
        )
        beneficiary_rows = list(
            (
                await db.execute(
                    select(Beneficiary).where(
                        Beneficiary.user_id == user.id,
                        Beneficiary.beneficiary_type == BeneficiaryTypeEnum.TRANSFER.value,
                    )
                )
            )
            .scalars()
            .all()
        )

    cache_invalidated = await _invalidate_seeded_user_cache(user.phone_number)

    print("\nDemo seed complete")
    print(f"Accounts: {len(account_rows)} total (created={created_accounts}, updated={updated_accounts})")
    for idx, acc in enumerate(account_rows, start=1):
        default_mark = " [default]" if acc.is_default else ""
        account_suffix = str(acc.account_number or "")[-4:]
        print(f"  {idx}. {acc.bank_name} (••••{account_suffix}) status={acc.mandate_status}{default_mark}")
    print(
        "Transfer beneficiaries: "
        f"{len(beneficiary_rows)} total "
        f"(created={created_beneficiaries}, updated={updated_beneficiaries}, "
        f"deleted_duplicates={deleted_duplicate_beneficiaries})"
    )
    print(f"Verified counterparty entities created: {created_counterparty_entities}")
    print(
        f"Canonical query projections rebuilt: bank_sources={rebuilt_bank_sources}, app_sources={rebuilt_app_sources}"
    )
    print(f"Variance acceptance source observations upserted: {seeded_variance_sources}")
    print(
        "Query semantic report: "
        f"sources={semantic_report['source_transactions']}, "
        f"projections={semantic_report['query_transactions']}, "
        f"events={semantic_report['economic_events']}, "
        f"coverage_windows={semantic_report['coverage_windows']}, "
        f"unresolved={semantic_report['unresolved_projections']}, "
        f"reconciliation={reconciliation_status}"
    )
    for idx, bene in enumerate(beneficiary_rows, start=1):
        alias_part = f" alias={bene.alias}" if bene.alias else ""
        print(f"  {idx}. Beneficiary {idx}{alias_part} -> {bene.bank_name} ••••{str(bene.account_number or '')[-4:]}")
    print(f"User-data cache invalidated: {cache_invalidated}")


async def _async_main(phone: str | None, *, reset_query_data: bool) -> None:
    user = await _resolve_target_user(phone)
    if reset_query_data:
        from scripts.reset_query_semantic_data import reset_query_semantic_data

        await reset_query_semantic_data(phone=user.phone_number)
    await _seed_for_user(user)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed 3 linked accounts and ambiguous beneficiaries for one user.")
    parser.add_argument("--phone", help="Optional phone number to target a specific user.")
    parser.add_argument(
        "--reset-query-data",
        action="store_true",
        help="Delete and rebuild query-side demo sources/projections for this non-production user.",
    )
    parser.add_argument("--yes", action="store_true", help="Required together with --reset-query-data.")
    args = parser.parse_args()
    if args.reset_query_data and not args.yes:
        parser.error("Pass --yes with --reset-query-data to acknowledge the destructive demo-data reset.")
    asyncio.run(_async_main(args.phone, reset_query_data=args.reset_query_data))


if __name__ == "__main__":
    main()
