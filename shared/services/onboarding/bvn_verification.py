"""BVN verification service for onboarding."""

from shared.clients.providers.mono import BankAccount, BvnLookupData, MonoApiError, mono_client
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

from .session import OnboardingStep, SessionManager

logger = get_logger(__name__)


class BvnVerificationService:
    """Handles BVN lookup, OTP sending, and verification."""

    def __init__(self, session_manager: SessionManager):
        self.session = session_manager

    async def get_session_data(self, flow_token: str) -> dict:
        """Get session data for a flow token."""
        return await self.session.get_session(flow_token)

    async def initiate_account_linking(self, flow_token: str, phone_number: str) -> dict:
        """
        Initiate account linking using stored BVN.

        Fetches user's BVN from database and starts verification.
        Returns available verification methods (phone, email).
        """
        bvn = None
        try:
            with UnitOfWork() as uow:
                if uow.users:
                    user = uow.users.get_by_phone(phone_number)
                    if user and user.extra_data:
                        bvn = user.extra_data.get("bvn")
        except Exception as e:
            logger.error("get_stored_bvn_error", error=str(e), phone=phone_number)

        if not bvn:
            return {"success": False, "error": "BVN not found. Please contact support."}

        logger.info("account_linking_initiated", phone=phone_number, bvn=bvn[:4] + "***")

        try:
            bvn_data: BvnLookupData = await mono_client.initiate_bvn_lookup(bvn)

            methods = [{"id": m.method, "title": m.hint} for m in bvn_data.methods]

            await self.session.update_session(
                flow_token,
                {
                    "phone_number": phone_number,
                    "bvn": bvn,
                    "session_id": bvn_data.session_id,
                    "methods": methods,
                    "step": OnboardingStep.METHOD_SELECTION.value,
                    "is_account_linking": True,
                },
            )

            logger.info("account_linking_bvn_verified", session_id=bvn_data.session_id[:8] + "...")

            return {"success": True, "data": {"bvn": bvn, "methods": methods}}

        except MonoApiError as e:
            logger.error("account_linking_bvn_failed", error=e.message)
            return {"success": False, "error": "Verification failed. Please try again."}

    async def initiate_bvn_verification(self, flow_token: str, bvn: str) -> dict:
        """
        Initiate BVN verification.

        Returns available verification methods (phone, email).
        """
        if not bvn or len(bvn) != 11 or not bvn.isdigit():
            return {"success": False, "error": "Invalid BVN. Please enter a valid 11-digit BVN."}

        logger.info("bvn_lookup_initiated", bvn=bvn[:4] + "***")

        try:
            bvn_data: BvnLookupData = await mono_client.initiate_bvn_lookup(bvn)

            methods = [{"id": m.method, "title": m.hint} for m in bvn_data.methods]

            is_linking = flow_token.startswith("link-")
            if is_linking:
                parts = flow_token.split("-")
                phone_number = parts[1] if len(parts) >= 2 else ""
            else:
                phone_number = flow_token.split("-")[-1] if flow_token else ""

            await self.session.update_session(
                flow_token,
                {
                    "phone_number": phone_number,
                    "bvn": bvn,
                    "session_id": bvn_data.session_id,
                    "methods": methods,
                    "step": OnboardingStep.METHOD_SELECTION.value,
                    "is_account_linking": is_linking,
                },
            )

            logger.info("bvn_lookup_success", session_id=bvn_data.session_id[:8] + "...")

            return {"success": True, "data": {"bvn": bvn, "methods": methods}}

        except MonoApiError as e:
            logger.error("bvn_lookup_failed", error=e.message)
            return {"success": False, "error": "BVN verification failed. Please try again."}

    async def send_otp(self, flow_token: str, method: str) -> dict:
        """Send OTP via a selected method (phone/email)."""
        logger.info("Got here via otp", method=method, flow_token=flow_token)
        if not method:
            return {"success": False, "error": "Please select a verification method."}

        session = await self.session.get_session(flow_token)
        logger.info("Session", session=session)
        session_id = session.get("session_id")
        if not session_id:
            return {"success": False, "error": "Session expired. Please start over."}

        try:
            await mono_client.verify_bvn(session_id, method)

            await self.session.update_session(
                flow_token,
                {
                    "selected_method": method,
                    "step": OnboardingStep.OTP_VERIFICATION.value,
                },
            )

            logger.info("otp_sent", method=method)

            return {"success": True, "data": {"bvn": session.get("bvn")}}

        except MonoApiError as e:
            logger.error("send_otp_failed", error=e.message)
            return {
                "success": False,
                "error": "Failed to send OTP. Please try again.",
                "data": {"methods": session.get("methods"), "bvn": session.get("bvn")},
            }

    async def verify_otp(self, flow_token: str, otp: str) -> dict:
        """Verify OTP and fetch bank accounts."""
        if not otp or len(otp) != 6 or not otp.isdigit():
            return {"success": False, "error": "Invalid OTP. Please enter a 6-digit code."}

        session = await self.session.get_session(flow_token)
        session_id = session.get("session_id")
        if not session_id:
            return {"success": False, "error": "Session expired. Please start over."}

        try:
            accounts: list[BankAccount] = await mono_client.verify_otp(session_id, otp)

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
                {"id": acc["id"], "title": f"{acc['bank_name']} - {acc['account_number']}"} for acc in accounts_data
            ]

            await self.session.update_session(
                flow_token,
                {
                    "otp_verified": True,
                    "accounts": accounts_data,
                    "step": OnboardingStep.ACCOUNT_SELECTION.value,
                },
            )

            logger.info("otp_verified", account_count=len(accounts))

            return {
                "success": True,
                "data": {
                    "bvn": session.get("bvn"),
                    "accounts": accounts_for_flow,
                },
            }

        except MonoApiError as e:
            logger.error("otp_verification_failed", error=e.message)
            return {"success": False, "error": "OTP verification failed. Please try again."}
