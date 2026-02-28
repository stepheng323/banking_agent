"""Seed linked accounts and ambiguous beneficiaries for local testing.

Usage:
    PYTHONPATH=. uv run python -m scripts.seed_user_test_data
    PYTHONPATH=. uv run python -m scripts.seed_user_test_data --phone 2348000000000
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from shared.database.connection import get_session_local
from shared.database.enums import BeneficiaryTypeEnum
from shared.database.models import Account, Beneficiary, User


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
    # Similar names on purpose to trigger beneficiary disambiguation for inputs like "Tolu".
    return [
        {
            "account_name": "Tolu Adebayo",
            "alias": "Tolu Access",
            "account_number": "2010000001",
            "bank_code": "044",
            "bank_name": "Access Bank",
        },
        {
            "account_name": "Tolu Adeyemi",
            "alias": "Tolu GTB",
            "account_number": "2010000002",
            "bank_code": "058",
            "bank_name": "GTBank",
        },
        {
            "account_name": "Tolulope Johnson",
            "alias": "Tolu First",
            "account_number": "2010000003",
            "bank_code": "011",
            "bank_name": "First Bank",
        },
    ]


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
            result = await db.execute(
                select(Beneficiary).where(
                    Beneficiary.user_id == user.id,
                    Beneficiary.beneficiary_type == BeneficiaryTypeEnum.TRANSFER.value,
                    Beneficiary.account_number == spec["account_number"],
                    Beneficiary.bank_code == spec["bank_code"],
                )
            )
            beneficiary = result.scalars().first()
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
                beneficiary.bank_name = spec["bank_name"]
                updated_beneficiaries += 1

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

    print("\nSeed complete")
    print(f"User: {user.phone_number} ({user.id})")
    print(f"Accounts: {len(account_rows)} total (created={created_accounts}, updated={updated_accounts})")
    for idx, acc in enumerate(account_rows, start=1):
        default_mark = " [default]" if acc.is_default else ""
        print(f"  {idx}. {acc.bank_name} ({acc.account_number}) status={acc.mandate_status}{default_mark}")
    print(
        "Transfer beneficiaries: "
        f"{len(beneficiary_rows)} total "
        f"(created={created_beneficiaries}, updated={updated_beneficiaries})"
    )
    for idx, bene in enumerate(beneficiary_rows, start=1):
        alias_part = f" alias={bene.alias}" if bene.alias else ""
        print(f"  {idx}. {bene.account_name}{alias_part} -> {bene.bank_name} {bene.account_number}")


async def _async_main(phone: str | None) -> None:
    user = await _resolve_target_user(phone)
    await _seed_for_user(user)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed 3 linked accounts and ambiguous beneficiaries for one user.")
    parser.add_argument("--phone", help="Optional phone number to target a specific user.")
    args = parser.parse_args()
    asyncio.run(_async_main(args.phone))


if __name__ == "__main__":
    main()
