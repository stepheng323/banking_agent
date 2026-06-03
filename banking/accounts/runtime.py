"""Runtime factory for account-domain workers."""

from langchain_core.language_models import BaseChatModel

from banking.accounts.management.worker import AccountWorker
from banking.accounts.repositories.account_repository import AccountRepository
from banking.identity.repositories.user_repository import UserRepository
from shared.cache.flow_session_manager import FlowSessionManager
from shared.clients.abstractions.banking import BankDataProvider
from shared.clients.abstractions.direct_debit import DirectDebitProvider


def build_account_worker(
    *,
    account_repo: AccountRepository,
    user_repo: UserRepository,
    llm: BaseChatModel,
    banking_provider: BankDataProvider,
    session_manager: FlowSessionManager,
    direct_debit_provider: DirectDebitProvider,
) -> AccountWorker:
    """Build the account worker through the account domain boundary."""
    return AccountWorker(
        account_repo=account_repo,
        user_repo=user_repo,
        llm=llm,
        banking_provider=banking_provider,
        session_manager=session_manager,
        direct_debit_provider=direct_debit_provider,
    )
