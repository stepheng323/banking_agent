"""BVN verification service for onboarding."""

from typing import List

from shared.clients.mono import mono_client, MonoApiError, BvnLookupData, BankAccount
from shared.utils.logging import get_logger

from .session import SessionManager, OnboardingStep

logger = get_logger(__name__)


class BvnVerificationService:
    """Handles BVN lookup, OTP sending, and verification."""
    
    def __init__(self, session_manager: SessionManager):
        self.session = session_manager
    
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
            
            phone_number = flow_token.split("-")[-1] if flow_token else ""
            await self.session.update_session(flow_token, {
                "phone_number": phone_number,
                "bvn": bvn,
                "session_id": bvn_data.session_id,
                "methods": methods,
                "step": OnboardingStep.METHOD_SELECTION.value,
            })

            logger.info("bvn_lookup_success", session_id=bvn_data.session_id[:8] + "...")
            
            return {"success": True, "data": {"bvn": bvn, "methods": methods}}

        except MonoApiError as e:
            logger.error("bvn_lookup_failed", error=e.message)
            return {"success": False, "error": "BVN verification failed. Please try again."}
    
    async def send_otp(self, flow_token: str, method: str) -> dict:
        """Send OTP via selected method (phone/email)."""
        if not method:
            return {"success": False, "error": "Please select a verification method."}

        session = await self.session.get_session(flow_token)
        if not session or not session.session_id:
            return {"success": False, "error": "Session expired. Please start over."}

        logger.info("sending_otp", method=method)

        try:
            await mono_client.verify_bvn(session.session_id, method)
            
            await self.session.update_session(flow_token, {
                "selected_method": method,
                "step": OnboardingStep.OTP_VERIFICATION.value,
            })

            logger.info("otp_sent", method=method)
            
            return {"success": True, "data": {"bvn": session.bvn}}

        except MonoApiError as e:
            logger.error("send_otp_failed", error=e.message)
            return {
                "success": False, 
                "error": "Failed to send OTP. Please try again.",
                "data": {"methods": session.methods, "bvn": session.bvn}
            }
    
    async def verify_otp(self, flow_token: str, otp: str) -> dict:
        """Verify OTP and fetch bank accounts."""
        if not otp or len(otp) != 6 or not otp.isdigit():
            return {"success": False, "error": "Invalid OTP. Please enter a 6-digit code."}

        session = await self.session.get_session(flow_token)
        if not session or not session.session_id:
            return {"success": False, "error": "Session expired. Please start over."}

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

            await self.session.update_session(flow_token, {
                "otp_verified": True,
                "accounts": accounts_data,
                "step": OnboardingStep.ACCOUNT_SELECTION.value,
            })

            logger.info("otp_verified", account_count=len(accounts))
            
            return {"success": True, "data": {
                "bvn": session.bvn,
                "accounts": accounts_for_flow,
            }}

        except MonoApiError as e:
            logger.error("otp_verification_failed", error=e.message)
            return {"success": False, "error": "OTP verification failed. Please try again."}
