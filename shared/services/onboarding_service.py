"""Onboarding service for BVN verification and account linking."""

from typing import Optional, List
from dataclasses import dataclass
from enum import Enum

from shared.clients.mono_client import mono_client, MonoApiError, BvnLookupData, BankAccount
from shared.cache.redis_client import RedisClient
from shared.models import CreateAccount, UserCreate, UserUpdate
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils import hash_plaintext, is_valid_pin_format
from shared.utils.logging import get_logger
import json

logger = get_logger(__name__)

SESSION_TTL = 3600  # 1 hour


class OnboardingStep(str, Enum):
    BVN_ENTRY = "bvn_entry"
    METHOD_SELECTION = "method_selection"
    OTP_VERIFICATION = "otp_verification"
    ACCOUNT_SELECTION = "account_selection"
    PIN_ENTRY = "pin_entry"
    COMPLETE = "complete"


@dataclass
class OnboardingSession:
    """Onboarding session state."""
    phone_number: str
    bvn: Optional[str] = None
    session_id: Optional[str] = None
    methods: Optional[List[dict]] = None
    selected_method: Optional[str] = None
    otp_verified: bool = False
    accounts: Optional[List[dict]] = None
    selected_accounts: Optional[List[str]] = None
    step: OnboardingStep = OnboardingStep.BVN_ENTRY


@dataclass
class ServiceResult:
    """Result from service operations."""
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class OnboardingService:
    """Service for handling user onboarding flow."""

    def __init__(self, redis: Optional[RedisClient] = None):
        self.redis = redis or RedisClient.get_client()

    def _session_key(self, flow_token: str) -> str:
        return f"onboarding:{flow_token}"

    async def get_session(self, flow_token: str) -> Optional[OnboardingSession]:
        """Get onboarding session from Redis."""
        try:
            data = await self.redis.get(self._session_key(flow_token))
            if data:
                parsed = json.loads(data)
                return OnboardingSession(**parsed)
        except Exception as e:
            logger.error("get_session_error", error=str(e))
        return None

    async def _get_session_data(self, flow_token: str) -> dict:
        """Get raw session data from Redis."""
        try:
            data = await self.redis.get(self._session_key(flow_token))
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error("get_session_error", error=str(e))
        return {}

    async def update_session(self, flow_token: str, updates: dict) -> None:
        """Merge updates into existing session."""
        try:
            existing = await self._get_session_data(flow_token)
            existing.update(updates)
            await self.redis.set(self._session_key(flow_token), json.dumps(existing), ex=SESSION_TTL)
        except Exception as e:
            logger.error("update_session_error", error=str(e))

    async def initiate_bvn_verification(self, flow_token: str, bvn: str) -> ServiceResult:
        """
        Initiate BVN verification.
        
        Returns available verification methods (phone, email).
        """
        if not bvn or len(bvn) != 11 or not bvn.isdigit():
            return ServiceResult(success=False, error="Invalid BVN. Please enter a valid 11-digit BVN.")

        logger.info("bvn_lookup_initiated", bvn=bvn[:4] + "***")

        try:
            bvn_data: BvnLookupData = await mono_client.initiate_bvn_lookup(bvn)
            
            methods = [{"id": m.method, "title": m.hint} for m in bvn_data.methods]
            
            phone_number = flow_token.split("-")[-1] if flow_token else ""
            await self.update_session(flow_token, {
                "phone_number": phone_number,
                "bvn": bvn,
                "session_id": bvn_data.session_id,
                "methods": methods,
                "step": OnboardingStep.METHOD_SELECTION.value,
            })

            logger.info("bvn_lookup_success", session_id=bvn_data.session_id[:8] + "...")
            
            return ServiceResult(success=True, data={"bvn": bvn, "methods": methods})

        except MonoApiError as e:
            logger.error("bvn_lookup_failed", error=e.message)
            return ServiceResult(success=False, error="BVN verification failed. Please try again.")

    async def send_otp(self, flow_token: str, method: str) -> ServiceResult:
        """
        Send OTP via selected method (phone/email).
        """
        if not method:
            return ServiceResult(success=False, error="Please select a verification method.")

        session = await self.get_session(flow_token)
        if not session or not session.session_id:
            return ServiceResult(success=False, error="Session expired. Please start over.")

        logger.info("sending_otp", method=method)

        try:
            await mono_client.verify_bvn(session.session_id, method)
            
            await self.update_session(flow_token, {
                "selected_method": method,
                "step": OnboardingStep.OTP_VERIFICATION.value,
            })

            logger.info("otp_sent", method=method)
            
            return ServiceResult(success=True, data={"bvn": session.bvn})

        except MonoApiError as e:
            logger.error("send_otp_failed", error=e.message)
            return ServiceResult(
                success=False, 
                error="Failed to send OTP. Please try again.",
                data={"methods": session.methods, "bvn": session.bvn}
            )

    async def verify_otp(self, flow_token: str, otp: str) -> ServiceResult:
        """
        Verify OTP and fetch bank accounts.
        """
        if not otp or len(otp) != 6 or not otp.isdigit():
            return ServiceResult(success=False, error="Invalid OTP. Please enter a 6-digit code.")

        session = await self.get_session(flow_token)
        if not session or not session.session_id:
            return ServiceResult(success=False, error="Session expired. Please start over.")

        try:
            accounts: List[BankAccount] = await mono_client.verify_otp(session.session_id, otp)
            
            accounts_data = [
                {
                    "id": f"{acc.institution.bank_code}_{acc.account_number}",
                    "account_number": acc.account_number,
                    "bank_name": acc.institution.name,
                    "bank_code": acc.institution.bank_code,
                    "account_name": acc.account_name,
                    "account_type": acc.account_type,
                }
                for acc in accounts
            ]
            
            accounts_for_flow = [
                {"id": acc["id"], "title": f"{acc['bank_name']} - {acc['account_number']}"}
                for acc in accounts_data
            ]

            await self.update_session(flow_token, {
                "otp_verified": True,
                "accounts": accounts_data,
                "step": OnboardingStep.ACCOUNT_SELECTION.value,
            })

            logger.info("otp_verified", account_count=len(accounts))
            
            return ServiceResult(success=True, data={
                "bvn": session.bvn,
                "accounts": accounts_for_flow,
            })

        except MonoApiError as e:
            logger.error("otp_verification_failed", error=e.message)
            return ServiceResult(success=False, error="OTP verification failed. Please try again.")

    async def select_accounts(self, flow_token: str, account_ids: List[str]) -> ServiceResult:
        """
        Store selected accounts for linking.
        """
        if not account_ids:
            return ServiceResult(success=False, error="Please select at least one account.")

        session = await self.get_session(flow_token)
        if not session:
            return ServiceResult(success=False, error="Session expired. Please start over.")

        await self.update_session(flow_token, {
            "selected_accounts": account_ids,
            "step": OnboardingStep.PIN_ENTRY.value,
        })

        return ServiceResult(success=True, data={
            "bvn": session.bvn,
            "selected_accounts": account_ids,
        })

    async def complete_onboarding(self, flow_token: str, pin: str) -> ServiceResult:
        """
        Complete onboarding by creating user and linking accounts.
        """
        if not pin or not is_valid_pin_format(pin):
            return ServiceResult(success=False, error="Invalid PIN. Please enter a 4 or 6-digit PIN.")

        session = await self.get_session(flow_token)
        if not session:
            return ServiceResult(success=False, error="Session expired. Please start over.")

        logger.info("onboarding_completed", flow_token=flow_token)  

        phone_number = session.phone_number or flow_token.split("-")[-1]
        if not phone_number:
            return ServiceResult(success=False, error="Phone number missing.")

        selected_accounts = []
        if session.accounts and session.selected_accounts:
            for acc in session.accounts:
                if acc["id"] in session.selected_accounts:
                    selected_accounts.append(acc)

        hashed_pin = hash_plaintext(pin)

        try:
            with UnitOfWork() as uow:
                if not uow.users or not uow.accounts:
                    return ServiceResult(success=False, error="Database error.")

                user = uow.users.get_by_phone(phone_number)
                if not user:
                    return ServiceResult(success=False, error="User not found. Please start over.")

                uow.users.update_user(str(user.id), UserUpdate(
                    full_name= selected_accounts[0].get("account_name", ""),
                    transaction_pin=hashed_pin,
                    onboarding_status="onboarding_completed",
                    extra_data=session,
                ))

                for acc in selected_accounts:
                    existing_account = uow.accounts.get_by_account_id(acc["id"])
                    
                    should_create = True
                    if existing_account is not None:
                        if getattr(existing_account, "user_id", None) == str(user.id):
                            should_create = False

                    if should_create:
                        uow.accounts.create_account(
                            CreateAccount(
                                user_id=str(user.id),
                                account_id=acc["id"],
                                account_number=acc.get("account_number", ""),
                                bank_name=acc.get("bank_name", ""),
                                account_name=acc.get("account_name", ""),
                                extra_data=acc,
                            )
                        )

            await self.update_session(flow_token, {"step": OnboardingStep.COMPLETE.value})\

            return ServiceResult(success=True, data={
                "phone_number": phone_number,
                "bvn": session.bvn,
                "accounts": selected_accounts,
                "accounts_count": len(selected_accounts),
            })

        except Exception as e:
            logger.error("onboarding_complete_error", error=str(e))
            return ServiceResult(success=False, error="Failed to complete onboarding. Please try again.")


onboarding_service = OnboardingService()

