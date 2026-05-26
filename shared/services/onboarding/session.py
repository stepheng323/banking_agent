"""Onboarding session types."""

from dataclasses import dataclass
from enum import Enum


class OnboardingStep(str, Enum):
    """Steps in the onboarding flow."""

    BVN_ENTRY = "bvn_entry"
    METHOD_SELECTION = "method_selection"
    OTP_VERIFICATION = "otp_verification"
    ACCOUNT_SELECTION = "account_selection"
    PIN_ENTRY = "pin_entry"
    COMPLETE = "complete"


@dataclass
class OnboardingSession:
    """Onboarding session state (typed wrapper for dict data)."""

    phone_number: str
    bvn: str | None = None
    session_id: str | None = None
    methods: list[dict] | None = None
    selected_method: str | None = None
    otp_verified: bool = False
    accounts: list[dict] | None = None
    selected_account: str | None = None
    email: str | None = None
    address: str | None = None
    step: OnboardingStep = OnboardingStep.BVN_ENTRY
    is_account_linking: bool = False
    channel: str | None = None
