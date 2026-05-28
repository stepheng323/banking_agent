"""Runtime instances for onboarding flows."""

from shared.cache.flow_session_manager import FlowSessionManager
from shared.services.onboarding.account_add import AccountAddService
from shared.services.onboarding.account_linking import AccountLinkingService
from shared.services.onboarding.bvn_verification import BvnVerificationService
from shared.services.onboarding.mandate import MandateService

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
