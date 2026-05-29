"""Unit of Work pattern for managing database transactions."""

from sqlalchemy.ext.asyncio import AsyncSession

from banking.accounts.repositories.account_repository import AccountRepository
from banking.beneficiaries.repositories.beneficiary_repository import BeneficiaryRepository
from banking.identity.repositories.user_repository import UserRepository
from banking.messaging.repositories.actionable_message_repository import ActionableMessageRepository
from banking.risk.repositories import RiskDecisionRepository
from banking.scheduling.repositories.scheduled_instruction_repository import ScheduledInstructionRepository
from banking.scheduling.repositories.scheduled_run_repository import ScheduledRunRepository
from banking.support.repositories.support_ticket_repository import SupportTicketRepository
from banking.transactions.repositories.bank_transaction_coverage_repository import BankTransactionCoverageRepository
from banking.transactions.repositories.bank_transaction_repository import BankTransactionRepository
from banking.transactions.repositories.transaction_repository import TransactionRepository
from banking.transfers.repositories.funded_transfer_repository import FundedTransferRepository
from banking.transfers.repositories.funding_step_repository import FundingStepRepository
from banking.webhooks.repositories.processed_webhook_event_repository import ProcessedWebhookEventRepository
from shared.database.connection import get_db_session


class UnitOfWork:
    """Manages database transactions and repositories (Async)."""

    def __init__(self) -> None:
        self.db: AsyncSession | None = None
        self.users: UserRepository | None = None
        self.accounts: AccountRepository | None = None
        self.beneficiaries: BeneficiaryRepository | None = None
        self.transactions: TransactionRepository | None = None
        self.funded_transfers: FundedTransferRepository | None = None
        self.funding_steps: FundingStepRepository | None = None
        self.scheduled_instructions: ScheduledInstructionRepository | None = None
        self.scheduled_runs: ScheduledRunRepository | None = None
        self.actionable_messages: ActionableMessageRepository | None = None
        self.bank_transactions: BankTransactionRepository | None = None
        self.bank_transaction_coverages: BankTransactionCoverageRepository | None = None
        self.processed_webhook_events: ProcessedWebhookEventRepository | None = None
        self.support_tickets: SupportTicketRepository | None = None
        self.risk_decisions: RiskDecisionRepository | None = None
        self._rolled_back = False

    async def __aenter__(self):
        """Enter transaction context."""
        self.db = get_db_session()
        self.users = UserRepository(self.db)
        self.accounts = AccountRepository(self.db)
        self.beneficiaries = BeneficiaryRepository(self.db)
        self.transactions = TransactionRepository(self.db)
        self.funded_transfers = FundedTransferRepository(self.db)
        self.funding_steps = FundingStepRepository(self.db)
        self.scheduled_instructions = ScheduledInstructionRepository(self.db)
        self.scheduled_runs = ScheduledRunRepository(self.db)
        self.actionable_messages = ActionableMessageRepository(self.db)
        self.bank_transactions = BankTransactionRepository(self.db)
        self.bank_transaction_coverages = BankTransactionCoverageRepository(self.db)
        self.processed_webhook_events = ProcessedWebhookEventRepository(self.db)
        self.support_tickets = SupportTicketRepository(self.db)
        self.risk_decisions = RiskDecisionRepository(self.db)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Exit transaction context and commit or rollback."""
        assert self.db is not None
        try:
            if exc_type:
                await self.db.rollback()
                self._rolled_back = True
            else:
                await self.db.commit()
        finally:
            await self.db.close()
        return False

    async def commit(self):
        """Manually commit transaction."""
        assert self.db is not None
        if not self._rolled_back:
            await self.db.commit()

    async def rollback(self):
        """Manually rollback transaction."""
        assert self.db is not None
        await self.db.rollback()
        self._rolled_back = True
