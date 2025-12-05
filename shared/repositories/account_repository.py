"""Repository for Account model."""

from typing import List, Optional, Union
from uuid import UUID

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
        # Convert string user_id to UUID if needed (Account.user_id is UUID type)
        user_uuid: Union[str, UUID] = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        return self.db.query(Account).filter(Account.user_id == user_uuid).all()


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

    def get_default_account(self, user_id: str) -> Optional[Account]:
        """Get user's default account."""
        user_uuid: Union[str, UUID] = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        
        return (
            self.db.query(Account)
            .filter(Account.user_id == user_uuid, Account.is_default == True)
            .first()
        )

    def set_default_account(self, user_id: str, account_id: str) -> Account:
        """
        Set an account as the default for a user.
        Unsets any existing default account.
        """
        user_uuid: Union[str, UUID] = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        
        # Unset all existing defaults for this user
        self.db.query(Account).filter(
            Account.user_id == user_uuid,
            Account.is_default == True
        ).update({"is_default": False})
        
        # Set new default
        account = self.get_by_account_id(account_id)
        if account and str(account.user_id) == str(user_uuid):
            account.is_default = True
            self.db.flush()
            return account
        
        raise ValueError(f"Account {account_id} not found for user {user_id}")

    def delete_account(self, account_id: str, user_id: str) -> bool:
        """
        Delete (unlink) an account.
        Returns True if successful, False otherwise.
        """
        user_uuid: Union[str, UUID] = user_id
        if isinstance(user_id, str):
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                pass
        
        account = self.get_by_account_id(account_id)
        if account and str(account.user_id) == str(user_uuid):
            # If this was the default account, unset default
            was_default = account.is_default
            
            self.db.delete(account)
            self.db.flush()
            
            # If it was default, set first remaining account as default
            if was_default:
                remaining_accounts = self.get_by_user(user_id)
                if remaining_accounts:
                    remaining_accounts[0].is_default = True
                    self.db.flush()
            
            return True
        
        return False
