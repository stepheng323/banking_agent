"""Onboarding service for BVN verification and account linking."""

from typing import Optional, List
import asyncio

from dataclasses import dataclass
from enum import Enum

from shared.clients.mono import mono_client, MonoApiError, BvnLookupData, BankAccount
from shared.cache.redis_client import RedisClient
from shared.models import CreateAccount, UserCreate, UserUpdate
import uuid as uuid_module
from datetime import datetime, timedelta
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
    selected_account: Optional[str] = None  # Single account ID
    email: Optional[str] = None
    address: Optional[str] = None
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

    def _build_mandate_auth_message(
        self,
        account_number: str,
        bank_name: str,
        transfer_destinations: list,
    ) -> str:
        """Build WhatsApp message with mandate authorization instructions."""
        lines = [
            "📋 *One Last Step to Complete Setup*",
            "",
            f"To activate your {bank_name} account ending in {account_number[-4:]}, "
            "transfer ₦50 from that account to any of these accounts:",
            "",
        ]
        
        for dest in transfer_destinations:
            bank = getattr(dest, 'bank_name', dest.get('bank_name', 'Unknown'))
            acct = getattr(dest, 'account_number', dest.get('account_number', ''))
            lines.append(f"• *{bank}*: {acct}")
        
        lines.extend([
            "",
            "⚠️ Important:",
            "• Transfer must come from your linked account",
            "• Complete within 1 hour",
            "• This ₦50 goes to NIBSS for verification",
            "",
            "Once done, your account will be ready in about 1 hour!",
        ])
        
        return "\n".join(lines)

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

    async def select_account(self, flow_token: str, account_id: Optional[str]) -> ServiceResult:
        """Store selected account."""
        if not account_id:
            session = await self.get_session(flow_token)
            return ServiceResult(
                success=False, 
                error="Please select an account.",
                data={"accounts": session.accounts if session else []}
            )

        session = await self.get_session(flow_token)
        if not session:
            return ServiceResult(success=False, error="Session expired. Please start over.")

        await self.update_session(flow_token, {
            "selected_account": account_id,
            "step": OnboardingStep.PIN_ENTRY.value,
        })

        return ServiceResult(success=True, data={"bvn": session.bvn})

    async def complete_onboarding(
        self,
        flow_token: str,
        pin: Optional[str],
        email: Optional[str],
        address: Optional[str],
    ) -> ServiceResult:
        """Complete onboarding by creating customer and linking account."""
        if not pin or not is_valid_pin_format(pin):
            return ServiceResult(success=False, error="Invalid PIN. Please enter a 4 or 6-digit PIN.")

        if not email:
            return ServiceResult(success=False, error="Email address is required.")

        if not address:
            return ServiceResult(success=False, error="Address is required.")

        session = await self.get_session(flow_token)
        if not session:
            return ServiceResult(success=False, error="Session expired. Please start over.")

        phone_number = session.phone_number or flow_token.split("-")[-1]
        if not phone_number:
            return ServiceResult(success=False, error="Phone number missing.")

        selected_account = None
        if session.accounts and session.selected_account:
            for acc in session.accounts:
                if acc["id"] == session.selected_account:
                    selected_account = acc
                    break

        if not selected_account:
            return ServiceResult(success=False, error="No account selected.")

        account_name = selected_account.get("account_name", "")
        name_parts = account_name.split(" ", 1)
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        hashed_pin = hash_plaintext(pin)
        
        bank_code = selected_account.get("bank_code", "")
        if not bank_code:
            institution = selected_account.get("institution", {})
            bank_code = institution.get("bank_code", "")

        try:
            # Sync: Update user and create account (without Mono IDs yet)
            with UnitOfWork() as uow:
                if not uow.users or not uow.accounts:
                    return ServiceResult(success=False, error="Database error.")

                user = uow.users.get_by_phone(phone_number)
                if not user:
                    return ServiceResult(success=False, error="User not found. Please start over.")

                uow.users.update_user(str(user.id), UserUpdate(
                    full_name=account_name,
                    email=email,
                    address=address,
                    transaction_pin=hashed_pin,
                    onboarding_status="onboarding_completed",
                    extra_data={"bvn": session.bvn},
                ))

                existing_account = uow.accounts.get_by_account_id(selected_account["id"])
                if not existing_account or getattr(existing_account, "user_id", None) != str(user.id):
                    uow.accounts.create_account(
                        CreateAccount(
                            user_id=str(user.id),
                            account_id=selected_account["id"],
                            account_number=selected_account.get("account_number", ""),
                            bank_name=selected_account.get("bank_name", ""),
                            bank_code=bank_code,
                            account_name=account_name,
                            mandate_status="pending",
                            extra_data=selected_account,
                        )
                    )

            await self.update_session(flow_token, {"step": OnboardingStep.COMPLETE.value})

            asyncio.create_task(
                self._setup_mono_customer_and_mandate(
                    phone_number=phone_number,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    address=address,
                    bvn=session.bvn or "",
                    account_id=selected_account["id"],
                    account_number=selected_account.get("account_number", ""),
                    bank_code=bank_code,
                    bank_name=selected_account.get("bank_name", ""),
                )
            )

            return ServiceResult(success=True, data={
                "phone_number": phone_number,
                "bvn": session.bvn,
                "account": selected_account,
            })

        except Exception as e:
            logger.error("onboarding_complete_error", error=str(e))
            return ServiceResult(success=False, error="Failed to complete onboarding. Please try again.")

    async def _setup_mono_customer_and_mandate(
        self,
        phone_number: str,
        first_name: str,
        last_name: str,
        email: str,
        address: str,
        bvn: str,
        account_id: str,
        account_number: str,
        bank_code: str,
        bank_name: str,
    ) -> None:
        """Background task: Create Mono customer and mandate, then update DB and notify user."""
        from shared.clients.whatsapp_client import WhatsAppClient
        
        try:
            customer = await mono_client.create_customer(
                first_name=first_name,
                last_name=last_name,
                phone=phone_number,
                email=email,
                address=address,
                identity_number=bvn,
                identity_type="bvn",
            )
            logger.info("mono_customer_created_async", phone=phone_number, customer_id=customer.id)

            mandate_reference = f"FP-{uuid_module.uuid4().hex[:12].upper()}"
            start_date = datetime.utcnow().strftime("%Y-%m-%d")
            end_date = (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%d")

            mandate = await mono_client.create_mandate(
                customer_id=customer.id,
                account_number=account_number,
                bank_code=bank_code,
                amount=100000000,
                reference=mandate_reference,
                start_date=start_date,
                end_date=end_date,
            )
            logger.info("mono_mandate_created_async", mandate_id=mandate.id, phone=phone_number)

            with UnitOfWork() as uow:
                if uow.users and uow.accounts:
                    user = uow.users.get_by_phone(phone_number)
                    if user:
                        uow.users.update_user(str(user.id), UserUpdate(mono_customer_id=customer.id))
                    
                    account = uow.accounts.get_by_account_id(account_id)
                    if account:
                        account.mandate_id = mandate.id
                        account.mandate_status = "pending"

            whatsapp = WhatsAppClient()
            transfer_destinations = mandate.transfer_destinations or []
            auth_message = self._build_mandate_auth_message(
                account_number=account_number,
                bank_name=bank_name,
                transfer_destinations=transfer_destinations,
            )
            await whatsapp.send_text(to=phone_number, text=auth_message)

        except Exception as e:
            logger.error("mono_setup_background_error", error=str(e), phone=phone_number)


onboarding_service = OnboardingService()

