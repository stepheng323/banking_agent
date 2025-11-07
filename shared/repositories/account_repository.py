"""Repository for Account model."""

from typing import List, Optional

from sqlalchemy.orm import Session

from shared.database.models import Account
from shared.models.account import CreateAccount
from shared.repositories.base import BaseRepository


class AccountRepository(BaseRepository[Account]):
    """Repository for Account operations."""

    def __init__(self, db: Session):
        super().__init__(db, Account)

    def get_by_user(self, user_id: str) -> List[Account]:
        """Get all accounts for a user."""
        return self.db.query(Account).filter(Account.user_id == user_id).all()


    def get_by_account_id(self, account_id: str) -> Optional[Account]:
        """Get account by account_id (external ID)."""
        return self.db.query(Account).filter(Account.account_id == account_id).first()

    def create_account(
        self,
        create_account: CreateAccount,
    ) -> Account:
        """Create a bank account."""
        account_dict = create_account.model_dump(exclude_unset=True)
        if "extra_data" not in account_dict or account_dict["extra_data"] is None:
            account_dict["extra_data"] = {}

        account = Account(**account_dict)
        self.db.add(account)
        self.db.flush()
        return account

    def deactivate_account(self, account_id: str) -> Account:
        """Deactivate an account (doesn't commit)."""
        account = self.get_by_account_id(account_id)
        if account:
            account.is_active = False
        return account
