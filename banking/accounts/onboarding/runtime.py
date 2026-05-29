"""Runtime instances for onboarding flows."""

from shared.cache.flow_session_manager import FlowSessionManager
from banking.accounts.onboarding.account_add import AccountAddService
from banking.accounts.onboarding.account_linking import AccountLinkingService
from banking.accounts.onboarding.bvn_verification import BvnVerificationService
from banking.accounts.onboarding.mandate import MandateService

session_manager = FlowSessionManager(key_prefix="onboarding")
mandate_service = MandateService()
bvn_service = BvnVerificationService(session_manager)
account_service = AccountLinkingService(session_manager, mandate_service)
account_add_service = AccountAddService(session_manager, mandate_service)

__all__ = [
    "session_manager",
    "mandate_service",
    "bvn_service",
    "account_service",
    "account_add_service",
]
