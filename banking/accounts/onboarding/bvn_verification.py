"""BVN verification service for onboarding."""

import hashlib

from banking.persistence.unit_of_work import UnitOfWork
from shared.cache.flow_session_manager import FlowSessionManager
from shared.clients.abstractions.banking import BankDataProvider
from shared.clients.factories.providers import ProviderFactory
from shared.config.settings import settings
from shared.utils.logging import get_logger, log_fingerprint

from .session import OnboardingStep

logger = get_logger(__name__)


def _token_fingerprint(flow_token: str | None) -> str:
    if not flow_token:
        return ""
    return hashlib.sha256(str(flow_token).encode("utf-8")).hexdigest()[:16]


def _mask_phone(phone_number: str | None) -> str:
    if not phone_number:
        return ""
    normalized = str(phone_number).strip()
    if len(normalized) <= 4:
        return normalized
    return f"{normalized[:4]}***{normalized[-2:]}"


class BvnVerificationService:
    """Handles BVN lookup, OTP sending, and verification."""

    def __init__(self, session_manager: FlowSessionManager, banking_provider: BankDataProvider | None = None):
        self.session = session_manager
        self.banking_provider = banking_provider

    def _get_banking_provider(self) -> BankDataProvider:
        if self.banking_provider is None:
            provider = ProviderFactory.get_bank_data_provider()
            if provider is None:
                raise RuntimeError(
                    f"Account provider bank-data capability is not configured: {settings.account_provider_name}"
                )
            self.banking_provider = provider
        return self.banking_provider

    async def _get_existing_linked_account_ids(self, phone_number: str) -> set[str]:
        """Return linked account IDs for a user so relinking can exclude duplicates."""
        if not phone_number:
            return set()

        try:
            async with UnitOfWork() as uow:
                if not uow.users or not uow.accounts:
                    return set()

                user = await uow.users.get_by_phone(phone_number)
                if not user:
                    return set()

                accounts = await uow.accounts.get_by_user(str(user.id))
                return {account.account_id for account in accounts if account.account_id}
        except Exception as e:
            logger.error(
                "linked_account_lookup_failed",
                error_type=type(e).__name__,
                phone_hash=log_fingerprint(phone_number),
            )
            return set()
        return set()

    async def get_session_data(self, flow_token: str) -> dict:
        """Get session data for a flow token."""
        result = await self.session.read_session(flow_token)
        return result.data or {}

    async def get_session_status(self, flow_token: str):
        """Get detailed session read status for strict relink/session checks."""
        return await self.session.read_session(flow_token)

    async def initiate_account_linking(self, flow_token: str, phone_number: str) -> dict:
        """
        Initiate account linking using stored BVN.

        Fetches user's BVN from database and starts verification.
        Returns available verification methods (phone, email).
        """
        bvn = None
        try:
            async with UnitOfWork() as uow:
                if uow.users:
                    user = await uow.users.get_by_phone(phone_number)
                    if user and user.extra_data:
                        bvn = user.extra_data.get("bvn")
        except Exception as e:
            logger.error(
                "get_stored_bvn_error",
                error_type=type(e).__name__,
                phone_hash=log_fingerprint(phone_number),
            )

        if not bvn:
            return {"success": False, "error": "BVN not found. Please contact support."}

        logger.info(
            "account_linking_initiated",
            phone_hash=log_fingerprint(phone_number),
            bvn_hash=log_fingerprint(bvn),
        )

        try:
            bvn_data = await self._get_banking_provider().initiate_bvn_lookup(bvn)
            if not bvn_data.success or not bvn_data.session_id:
                return {"success": False, "error": "Verification failed. Please try again."}

            methods = [
                {"id": str(method.get("method") or ""), "title": str(method.get("hint") or "")}
                for method in bvn_data.verification_methods or []
            ]

            stored = await self.session.update_session_strict(
                flow_token,
                {
                    "phone_number": phone_number,
                    "bvn": bvn,
                    "session_id": bvn_data.session_id,
                    "methods": methods,
                    "step": OnboardingStep.METHOD_SELECTION.value,
                    "is_account_linking": True,
                },
                verify=True,
            )
            if not stored:
                return {"success": False, "error": "Session expired. Please start over."}

            logger.info("account_linking_bvn_verified", session_id_hash=log_fingerprint(bvn_data.session_id))

            return {"success": True, "data": {"bvn": bvn, "methods": methods}}

        except Exception as e:
            logger.error("account_linking_bvn_failed", error_type=type(e).__name__)
            return {"success": False, "error": "Verification failed. Please try again."}

    async def initiate_bvn_verification(self, flow_token: str, bvn: str) -> dict:
        """
        Initiate BVN verification.

        Returns available verification methods (phone, email).
        """
        if not bvn or len(bvn) != 11 or not bvn.isdigit():
            return {"success": False, "error": "Invalid BVN. Please enter a valid 11-digit BVN."}

        existing_result = await self.session.read_session(flow_token)
        existing_session = existing_result.data or {}
        phone_number = str(existing_session.get("phone_number") or "") if existing_result.found else ""
        if not phone_number:
            return {"success": False, "error": "Session expired. Please start over."}

        is_linking = bool(existing_session.get("is_account_linking")) or flow_token.startswith("link-")

        logger.info("bvn_lookup_initiated", bvn_hash=log_fingerprint(bvn))

        try:
            bvn_data = await self._get_banking_provider().initiate_bvn_lookup(bvn)
            if not bvn_data.success or not bvn_data.session_id:
                return {"success": False, "error": "BVN verification failed. Please try again."}

            methods = [
                {"id": str(method.get("method") or ""), "title": str(method.get("hint") or "")}
                for method in bvn_data.verification_methods or []
            ]

            stored = await self.session.update_session_strict(
                flow_token,
                {
                    "phone_number": phone_number,
                    "bvn": bvn,
                    "session_id": bvn_data.session_id,
                    "methods": methods,
                    "step": OnboardingStep.METHOD_SELECTION.value,
                    "is_account_linking": is_linking,
                },
                verify=True,
            )
            if not stored:
                return {"success": False, "error": "Session expired. Please start over."}

            logger.info("bvn_lookup_success", session_id_hash=log_fingerprint(bvn_data.session_id))

            return {"success": True, "data": {"bvn": bvn, "methods": methods}}

        except Exception as e:
            logger.error("bvn_lookup_failed", error_type=type(e).__name__)
            return {"success": False, "error": "BVN verification failed. Please try again."}

    async def send_otp(self, flow_token: str, method: str) -> dict:
        """Send OTP via a selected method (phone/email)."""
        logger.info("otp_send_requested", method=method, flow_token_hash=_token_fingerprint(flow_token))
        if not method:
            return {"success": False, "error": "Please select a verification method."}

        read_result = await self.session.read_session(flow_token)
        session = read_result.data or {}
        logger.info(
            "otp_session_loaded",
            flow_token_hash=_token_fingerprint(flow_token),
            step=session.get("step"),
            phone_masked=_mask_phone(session.get("phone_number")),
            has_bvn=bool(session.get("bvn")),
            has_session_id=bool(session.get("session_id")),
            has_accounts=bool(session.get("accounts")),
        )
        session_id = session.get("session_id")
        if not session_id:
            return {"success": False, "error": "Session expired. Please start over."}

        try:
            result = await self._get_banking_provider().verify_bvn(session_id, method)
            if not result.get("success"):
                return {
                    "success": False,
                    "error": "Failed to send OTP. Please try again.",
                    "data": {"methods": session.get("methods"), "bvn": session.get("bvn")},
                }

            stored = await self.session.update_session_strict(
                flow_token,
                {
                    "selected_method": method,
                    "step": OnboardingStep.OTP_VERIFICATION.value,
                },
                verify=True,
            )
            if not stored:
                return {"success": False, "error": "Session expired. Please start over."}

            logger.info("otp_sent", method=method)

            return {"success": True, "data": {"bvn": session.get("bvn")}}

        except Exception as e:
            logger.error("send_otp_failed", error_type=type(e).__name__)
            return {
                "success": False,
                "error": "Failed to send OTP. Please try again.",
                "data": {"methods": session.get("methods"), "bvn": session.get("bvn")},
            }

    async def verify_otp(self, flow_token: str, otp: str) -> dict:
        """Verify OTP and fetch bank accounts."""
        if not otp or len(otp) != 6 or not otp.isdigit():
            return {"success": False, "error": "Invalid OTP. Please enter a 6-digit code."}

        read_result = await self.session.read_session(flow_token)
        session = read_result.data or {}
        session_id = session.get("session_id")
        if not session_id:
            return {"success": False, "error": "Session expired. Please start over."}

        try:
            verification = await self._get_banking_provider().verify_otp(session_id, otp)
            if not verification.success:
                return {"success": False, "error": "OTP verification failed. Please try again."}
            accounts = verification.accounts or []
            existing_account_ids = await self._get_existing_linked_account_ids(session.get("phone_number", ""))

            accounts_data = [
                {
                    "id": f"{acc.get('bank_code')}_{acc.get('account_number')}",
                    "account_number": acc.get("account_number"),
                    "bank_name": acc.get("bank_name"),
                    "bank_code": acc.get("bank_code"),
                    "account_name": acc.get("account_name"),
                    "account_type": acc.get("account_type"),
                }
                for acc in accounts
                if f"{acc.get('bank_code')}_{acc.get('account_number')}" not in existing_account_ids
            ]

            accounts_for_flow = [
                {"id": acc["id"], "title": f"{acc['bank_name']} - {acc['account_number']}"} for acc in accounts_data
            ]

            if not accounts_data:
                logger.info(
                    "otp_verified_no_new_accounts",
                    phone_hash=log_fingerprint(session.get("phone_number")),
                    total_accounts=len(accounts),
                    filtered_accounts=len(existing_account_ids),
                )
                return {
                    "success": False,
                    "error": "No new accounts available to link.",
                }

            stored = await self.session.update_session_strict(
                flow_token,
                {
                    "otp_verified": True,
                    "accounts": accounts_data,
                    "step": OnboardingStep.ACCOUNT_SELECTION.value,
                },
                verify=True,
            )
            if not stored:
                return {"success": False, "error": "Session expired. Please start over."}

            logger.info("otp_verified", account_count=len(accounts))

            return {
                "success": True,
                "data": {
                    "bvn": session.get("bvn"),
                    "accounts": accounts_for_flow,
                },
            }

        except Exception as e:
            logger.error("otp_verification_failed", error_type=type(e).__name__)
            return {"success": False, "error": "OTP verification failed. Please try again."}
