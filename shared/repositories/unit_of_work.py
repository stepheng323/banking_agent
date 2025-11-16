"""Unit of Work pattern for managing database transactions."""

from typing import Optional

from sqlalchemy.orm import Session

from shared.database.connection import get_db_session
from shared.repositories.account_repository import AccountRepository
from shared.repositories.user_repository import UserRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.transaction_repository import TransactionRepository


class UnitOfWork:
    """Manages database transactions and repositories."""

    def __init__(self):
        self.db: Optional[Session] = None
        self.users: Optional[UserRepository] = None
        self.accounts: Optional[AccountRepository] = None
        self.beneficiaries: Optional[BeneficiaryRepository] = None
        self.transactions: Optional[TransactionRepository] = None
        self._rolled_back = False

    def __enter__(self):
        """Enter transaction context."""
        self.db = get_db_session()
        self.users = UserRepository(self.db)
        self.accounts = AccountRepository(self.db)
        self.beneficiaries = BeneficiaryRepository(self.db)
        self.transactions = TransactionRepository(self.db)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit transaction context and commit or rollback."""
        try:
            if exc_type:
                self.db.rollback()
                self._rolled_back = True
            else:
                self.db.commit()
        finally:
            self.db.close()
        return False

    def commit(self):
        """Manually commit transaction."""
        if not self._rolled_back:
            self.db.commit()

    def rollback(self):
        """Manually rollback transaction."""
        self.db.rollback()
        self._rolled_back = True
