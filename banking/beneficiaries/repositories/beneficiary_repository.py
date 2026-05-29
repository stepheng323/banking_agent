"""Repository for Beneficiary model."""

import re
import unicodedata
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from banking.persistence.base import BaseRepository
from shared.database.models import Beneficiary


def _normalize_bank_name(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", without_marks.lower()).strip()


class BeneficiaryRepository(BaseRepository[Beneficiary]):
    """Repository for Beneficiary operations."""

    def __init__(self, db: AsyncSession):
        super().__init__(db, Beneficiary)

    async def get_by_user(self, user_id: str, beneficiary_type: str | None = None) -> list[Beneficiary]:
        """Get all beneficiaries for a user, optionally filtered by type."""
        user_uuid: str | UUID = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        query = select(Beneficiary).filter(Beneficiary.user_id == user_uuid)
        if beneficiary_type:
            query = query.filter(Beneficiary.beneficiary_type == beneficiary_type)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_by_name(self, user_id: str, name: str, beneficiary_type: str | None = None) -> Beneficiary | None:
        """Get a beneficiary by exact name or alias match, optionally filtered by type."""
        user_uuid: str | UUID = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        query = select(Beneficiary).filter(
            Beneficiary.user_id == user_uuid,
            or_(Beneficiary.account_name == name, Beneficiary.alias == name),
        )
        if beneficiary_type:
            query = query.filter(Beneficiary.beneficiary_type == beneficiary_type)
        result = await self.db.execute(query)
        return result.scalars().first()

    async def search_by_name(
        self, user_id: str, search_term: str, beneficiary_type: str | None = None
    ) -> list[Beneficiary]:
        """Search beneficiaries by name/alias (case-insensitive partial match).

        Optionally filtered by beneficiary type.
        """
        user_uuid: str | UUID = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        search_pattern = f"%{search_term}%"
        query = select(Beneficiary).filter(
            Beneficiary.user_id == user_uuid,
            or_(
                Beneficiary.account_name.ilike(search_pattern),
                Beneficiary.alias.ilike(search_pattern),
            ),
        )
        if beneficiary_type:
            query = query.filter(Beneficiary.beneficiary_type == beneficiary_type)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_transfer_by_account(
        self,
        user_id: str,
        account_number: str,
        bank_code: str | None,
    ) -> Beneficiary | None:
        """Get a saved transfer beneficiary by account and optional bank code."""
        user_uuid: str | UUID = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        query = select(Beneficiary).filter(
            Beneficiary.user_id == user_uuid,
            Beneficiary.beneficiary_type == "transfer",
            Beneficiary.account_number == account_number,
        )
        if bank_code:
            query = query.filter(Beneficiary.bank_code == bank_code)
        result = await self.db.execute(query.order_by(Beneficiary.created_at.asc()).limit(1))
        return result.scalars().first()

    async def should_suggest_beneficiary(
        self,
        user_id: str,
        account_number: str,
        bank_code: str | None,
        bank_name: str | None = None,
        beneficiary_type: str = "transfer",
    ) -> bool:
        """Check if recipient should be suggested as a beneficiary."""
        beneficiaries = await self.get_by_user(user_id, beneficiary_type=beneficiary_type)
        if bank_code:
            # Handle None values in comparisons - skip if either field is None.
            return not any(
                beneficiary.account_number == account_number and beneficiary.bank_code == bank_code
                for beneficiary in beneficiaries
                if beneficiary.account_number is not None and beneficiary.bank_code is not None
            )

        normalized_bank_name = _normalize_bank_name(bank_name)
        if not normalized_bank_name:
            return True

        return not any(
            beneficiary.account_number == account_number
            and _normalize_bank_name(beneficiary.bank_name) == normalized_bank_name
            for beneficiary in beneficiaries
            if beneficiary.account_number is not None and beneficiary.bank_name is not None
        )

    async def should_suggest_mobile_beneficiary(
        self,
        user_id: str,
        phone_number: str,
        network: str,
    ) -> bool:
        """Check if a mobile-line beneficiary should be suggested."""
        airtime_beneficiaries = await self.get_by_user(user_id, beneficiary_type="airtime")
        data_beneficiaries = await self.get_by_user(user_id, beneficiary_type="data")
        beneficiaries = [*airtime_beneficiaries, *data_beneficiaries]
        return not any(
            beneficiary.account_number == phone_number and beneficiary.bank_name == network
            for beneficiary in beneficiaries
            if beneficiary.account_number is not None and beneficiary.bank_name is not None
        )
