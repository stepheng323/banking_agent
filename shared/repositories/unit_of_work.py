"""Unit of Work pattern for managing database transactions."""

from sqlalchemy.orm import Session

from shared.database.connection import get_db_session
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.funded_transfer_repository import FundedTransferRepository
from shared.repositories.funding_step_repository import FundingStepRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.repositories.user_repository import UserRepository


class UnitOfWork:
    """Manages database transactions and repositories."""

    def __init__(self):
        self.db: Session | None = None
        self.users: UserRepository | None = None
        self.accounts: AccountRepository | None = None
        self.beneficiaries: BeneficiaryRepository | None = None
        self.transactions: TransactionRepository | None = None
        self.funded_transfers: FundedTransferRepository | None = None
        self.funding_steps: FundingStepRepository | None = None
        self.actionable_messages: ActionableMessageRepository | None = None
        self._rolled_back = False

    def __enter__(self):
        """Enter transaction context."""
        self.db = get_db_session()
        self.users = UserRepository(self.db)
        self.accounts = AccountRepository(self.db)
        self.beneficiaries = BeneficiaryRepository(self.db)
        self.transactions = TransactionRepository(self.db)
        self.funded_transfers = FundedTransferRepository(self.db)
        self.funding_steps = FundingStepRepository(self.db)
        self.actionable_messages = ActionableMessageRepository(self.db)
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
