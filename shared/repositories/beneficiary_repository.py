"""Repository for Beneficiary model."""
from uuid import UUID
from typing import List, Optional, Union
from sqlalchemy.orm import Session
from sqlalchemy import or_
from shared.repositories.base import BaseRepository
from shared.database.models import Beneficiary


class BeneficiaryRepository(BaseRepository[Beneficiary]):
    """Repository for Beneficiary operations."""

    def __init__(self, db: Session):
        super().__init__(db, Beneficiary)

    def get_by_user(self, user_id: str, beneficiary_type: Optional[str] = None) -> List[Beneficiary]:
        """Get all beneficiaries for a user, optionally filtered by type."""
        user_uuid: Union[str, UUID] = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        query = self.db.query(Beneficiary).filter(
            Beneficiary.user_id == user_uuid)
        if beneficiary_type:
            query = query.filter(
                Beneficiary.beneficiary_type == beneficiary_type)
        return query.all()

    def get_by_name(self, user_id: str, name: str, beneficiary_type: Optional[str] = None) -> Optional[Beneficiary]:
        """Get a beneficiary by exact name or alias match, optionally filtered by type."""
        user_uuid: Union[str, UUID] = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        query = self.db.query(Beneficiary).filter(
            Beneficiary.user_id == user_uuid,
            or_(
                Beneficiary.account_name == name,
                Beneficiary.alias == name
            )
        )
        if beneficiary_type:
            query = query.filter(
                Beneficiary.beneficiary_type == beneficiary_type)
        return query.first()

    def search_by_name(self, user_id: str, search_term: str, beneficiary_type: Optional[str] = None) -> List[Beneficiary]:
        """Search beneficiaries by name or alias (case-insensitive partial match), optionally filtered by type."""
        user_uuid: Union[str, UUID] = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        search_pattern = f"%{search_term}%"
        query = self.db.query(Beneficiary).filter(
            Beneficiary.user_id == user_uuid,
            or_(
                Beneficiary.account_name.ilike(search_pattern),
                Beneficiary.alias.ilike(search_pattern)
            )
        )
        if beneficiary_type:
            query = query.filter(
                Beneficiary.beneficiary_type == beneficiary_type)
        return query.all()

    def get_all_for_user(self, user_id: str) -> List[Beneficiary]:
        """Get all beneficiaries for a user (alias for get_by_user)."""
        return self.get_by_user(user_id)

    def should_suggest_beneficiary(
        self,
        user_id: str,
        account_number: str,
        bank_code: str,
        beneficiary_type: str = "transfer",
    ) -> bool:
        """Check if recipient should be suggested as a beneficiary."""
        beneficiaries = self.get_by_user(
            user_id, beneficiary_type=beneficiary_type)
        # Handle None values in comparisons - skip if either field is None
        return not any(
            beneficiary.account_number == account_number and 
            beneficiary.bank_code == bank_code
            for beneficiary in beneficiaries
            if beneficiary.account_number is not None and beneficiary.bank_code is not None
        )

    def should_suggest_airtime_beneficiary(
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
        beneficiaries = self.get_by_user(user_id, beneficiary_type="airtime")
        # Handle None values in comparisons - skip if either field is None
        # For airtime: account_number=phone, bank_name=network (both should always be present)
        return not any(
            beneficiary.account_number == phone_number and 
            beneficiary.bank_name == network
            for beneficiary in beneficiaries
            if beneficiary.account_number is not None and beneficiary.bank_name is not None
        )
