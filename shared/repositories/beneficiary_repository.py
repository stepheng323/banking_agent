"""Repository for Beneficiary model."""
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_
from shared.repositories.base import BaseRepository
from shared.database.models import Beneficiary


class BeneficiaryRepository(BaseRepository[Beneficiary]):
    """Repository for Beneficiary operations."""

    def __init__(self, db: Session):
        super().__init__(db, Beneficiary)

    def get_by_user(self, user_id: str) -> List[Beneficiary]:
        """Get all beneficiaries for a user."""
        from uuid import UUID
        # Convert string UUID to UUID if needed
        if isinstance(user_id, str):
            try:
                user_id = UUID(user_id)
            except ValueError:
                pass
        return self.db.query(Beneficiary).filter(Beneficiary.user_id == user_id).all()

    def get_by_name(self, user_id: str, name: str) -> Optional[Beneficiary]:
        """Get a beneficiary by exact name or alias match."""
        from uuid import UUID
        # Convert string UUID to UUID if needed
        if isinstance(user_id, str):
            try:
                user_id = UUID(user_id)
            except ValueError:
                pass
        return self.db.query(Beneficiary).filter(
            Beneficiary.user_id == user_id,
            or_(
                Beneficiary.account_name == name,
                Beneficiary.alias == name
            )
        ).first()

    def search_by_name(self, user_id: str, search_term: str) -> List[Beneficiary]:
        """Search beneficiaries by name or alias (case-insensitive partial match)."""
        from uuid import UUID
        # Convert string UUID to UUID if needed
        if isinstance(user_id, str):
            try:
                user_id = UUID(user_id)
            except ValueError:
                pass
        search_pattern = f"%{search_term}%"
        return self.db.query(Beneficiary).filter(
            Beneficiary.user_id == user_id,
            or_(
                Beneficiary.account_name.ilike(search_pattern),
                Beneficiary.alias.ilike(search_pattern)
            )
        ).all()

    def get_all_for_user(self, user_id: str) -> List[Beneficiary]:
        """Get all beneficiaries for a user (alias for get_by_user)."""
        return self.get_by_user(user_id)
