"""Onboarding services package.

This package contains the modular onboarding flow components:
- SessionManager: Redis session management
- BvnVerificationService: BVN lookup and OTP verification
- MandateService: Mandate creation and reinitiation
- AccountLinkingService: Account selection and onboarding completion
- OnboardingService: Facade for backward compatibility
"""

from dataclasses import dataclass
from typing import Optional

from shared.cache.redis_client import RedisClient

from .session import SessionManager, OnboardingSession, OnboardingStep
from .bvn_verification import BvnVerificationService
from .mandate import MandateService
from .account_linking import AccountLinkingService


@dataclass
class ServiceResult:
    """Result from service operations."""
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class OnboardingService:
    """
    Facade for the onboarding flow.
    
    Maintains backward compatibility with the original monolithic service
    while delegating to focused sub-services.
    """
    
    def __init__(self, redis: Optional[RedisClient] = None):
        self.session = SessionManager(redis)
        self.mandate = MandateService()
        self.bvn = BvnVerificationService(self.session)
        self.account = AccountLinkingService(self.session, self.mandate)
    
    # ============ Session Methods ============
    
    async def get_session(self, flow_token: str) -> Optional[OnboardingSession]:
        return await self.session.get_session(flow_token)
    
    async def update_session(self, flow_token: str, updates: dict) -> None:
        await self.session.update_session(flow_token, updates)
    
    # ============ BVN Verification ============
    
    async def initiate_bvn_verification(self, flow_token: str, bvn: str) -> ServiceResult:
        result = await self.bvn.initiate_bvn_verification(flow_token, bvn)
        return ServiceResult(**result)
    
    async def send_otp(self, flow_token: str, method: str) -> ServiceResult:
        result = await self.bvn.send_otp(flow_token, method)
        return ServiceResult(**result)
    
    async def verify_otp(self, flow_token: str, otp: str) -> ServiceResult:
        result = await self.bvn.verify_otp(flow_token, otp)
        return ServiceResult(**result)
    
    # ============ Account Linking ============
    
    async def select_account(self, flow_token: str, account_id: Optional[str]) -> ServiceResult:
        result = await self.account.select_account(flow_token, account_id)
        return ServiceResult(**result)
    
    async def complete_onboarding(
        self,
        flow_token: str,
        pin: Optional[str],
        email: Optional[str],
        address: Optional[str],
    ) -> ServiceResult:
        result = await self.account.complete_onboarding(flow_token, pin, email, address)
        return ServiceResult(**result)
    
    # ============ Mandate Management ============
    
    async def reinitiate_mandate(self, phone_number: str, account_id: str) -> ServiceResult:
        result = await self.mandate.reinitiate_mandate(phone_number, account_id)
        return ServiceResult(**result)
    
    def _build_mandate_auth_message(
        self,
        account_number: str,
        bank_name: str,
        transfer_destinations: list,
        is_reinitiation: bool = False,
    ) -> str:
        return self.mandate.build_mandate_auth_message(
            account_number, bank_name, transfer_destinations, is_reinitiation
        )


# Singleton instance for backward compatibility
onboarding_service = OnboardingService()


__all__ = [
    "OnboardingService",
    "OnboardingSession", 
    "OnboardingStep",
    "ServiceResult",
    "SessionManager",
    "BvnVerificationService",
    "MandateService",
    "AccountLinkingService",
    "onboarding_service",
]
