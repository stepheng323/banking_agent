"""Onboarding services package.

This package contains the modular onboarding flow components:
- SessionManager: Redis session management
- BvnVerificationService: BVN lookup and OTP verification
- MandateService: Mandate creation and reinitiation
- AccountLinkingService: Account selection and onboarding completion
- AccountAddService: Adding accounts to existing users
"""

from dataclasses import dataclass
from typing import Optional

from .account_add import AccountAddService
from .account_linking import AccountLinkingService
from .bvn_verification import BvnVerificationService
from .mandate import MandateService
from .session import OnboardingSession, OnboardingStep, SessionManager


@dataclass
class ServiceResult:
    """Result from service operations."""

    success: bool
    data: dict | None = None
    error: str | None = None


_session_manager = SessionManager()
_mandate_service = MandateService()
_bvn_service = BvnVerificationService(_session_manager)
_account_service = AccountLinkingService(_session_manager, _mandate_service)
_account_add_service = AccountAddService(_session_manager, _mandate_service)


__all__ = [
    "SessionManager",
    "BvnVerificationService",
    "MandateService",
    "AccountLinkingService",
    "AccountAddService",
    "OnboardingSession",
    "OnboardingStep",
    "ServiceResult",
    "session_manager",
    "mandate_service",
    "bvn_service",
    "account_service",
    "account_add_service",
]


session_manager = _session_manager
mandate_service = _mandate_service
bvn_service = _bvn_service
account_service = _account_service
account_add_service = _account_add_service
