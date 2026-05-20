"""Repository for Beneficiary model."""

import re
import unicodedata
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.models import Beneficiary
from shared.repositories.base import BaseRepository


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

    async def get_all_for_user(self, user_id: str) -> list[Beneficiary]:
        """Get all beneficiaries for a user (alias for get_by_user)."""
        return await self.get_by_user(user_id)

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

    async def should_suggest_airtime_beneficiary(
        self,
        user_id: str,
        phone_number: str,
        network: str,
    ) -> bool:
        """
        Check if airtime recipient should be suggested as a beneficiary.

        For airtime beneficiaries:
        - account_number stores the phone number
        - bank_name stores the network name

        Args:
            user_id: User's UUID
            phone_number: Recipient phone number
            network: Network name (MTN, Airtel, Glo, 9mobile)

        Returns:
            True if recipient should be suggested (doesn't exist), False otherwise
        """
        beneficiaries = await self.get_by_user(user_id, beneficiary_type="airtime")
        # Handle None values in comparisons - skip if either field is None
        # For airtime: account_number=phone, bank_name=network (both should always be present)
        return not any(
            beneficiary.account_number == phone_number and beneficiary.bank_name == network
            for beneficiary in beneficiaries
            if beneficiary.account_number is not None and beneficiary.bank_name is not None
        )
