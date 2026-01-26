"""Account add service for adding accounts to existing users."""

import asyncio

from shared.cache.flow_session_manager import FlowSessionManager
from shared.models import CreateAccount
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.onboarding.mandate import MandateService
from shared.services.onboarding.session import OnboardingStep
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AccountAddService:
    """Handles adding new accounts to existing users (post-onboarding)."""

    def __init__(self, session_manager: FlowSessionManager, mandate_service: MandateService):
        self.session = session_manager
        self.mandate = mandate_service

    async def add_account(self, flow_token: str, account_id: str | None = None) -> dict:
        """
        Add a new account to an existing user.

        Called after BVN verification and account selection.
        Skips PIN/email/address since the user already has these.
        """
        session = await self.session.get_session(flow_token)
        if not session:
            return {"success": False, "error": "Session expired. Please try again."}

        phone_number = session.get("phone_number")
        if not phone_number:
            return {"success": False, "error": "Phone number missing."}

        accounts = session.get("accounts", [])
        selected_account_id = account_id or session.get("selected_account")
        if not selected_account_id or not accounts:
            return {"success": False, "error": "No account selected."}

        selected_account = None
        for acc in accounts:
            if acc["id"] == selected_account_id:
                selected_account = acc
                break

        if not selected_account:
            return {"success": False, "error": "Selected account not found."}

        bank_code = selected_account.get("bank_code", "")
        if not bank_code:
            institution = selected_account.get("institution", {})
            bank_code = institution.get("bank_code", "")

        try:
            with UnitOfWork() as uow:
                if not uow.users or not uow.accounts:
                    return {"success": False, "error": "Database error."}

                user = uow.users.get_by_phone(phone_number)
                if not user:
                    return {
                        "success": False,
                        "error": "User not found. Please complete onboarding first.",
                    }

                existing_account = uow.accounts.get_by_account_id(selected_account_id)
                if existing_account and str(existing_account.user_id) == str(user.id):
                    return {"success": False, "error": "This account is already linked."}

                uow.accounts.create_account(
                    CreateAccount(
                        user_id=str(user.id),
                        account_id=selected_account_id,
                        account_number=selected_account.get("account_number", ""),
                        bank_name=selected_account.get("bank_name", ""),
                        bank_code=bank_code,
                        account_name=selected_account.get("account_name", ""),
                        mandate_status="pending",
                        extra_data=selected_account,
                    )
                )

                mono_customer_id = user.mono_customer_id

            await self.session.update_session(flow_token, {"step": OnboardingStep.COMPLETE.value})

            if mono_customer_id:
                asyncio.create_task(
                    self._setup_mandate_for_account(
                        phone_number=phone_number,
                        mono_customer_id=mono_customer_id,
                        account_id=selected_account_id,
                        account_number=selected_account.get("account_number", ""),
                        bank_code=bank_code,
                        bank_name=selected_account.get("bank_name", ""),
                    )
                )

            return {
                "success": True,
                "data": {
                    "phone_number": phone_number,
                    "account": selected_account,
                },
            }

        except Exception as e:
            logger.error("add_account_error", error=str(e), phone=phone_number)
            return {"success": False, "error": "Failed to add account. Please try again."}

    async def _setup_mandate_for_account(
        self,
        phone_number: str,
        mono_customer_id: str,
        account_id: str,
        account_number: str,
        bank_code: str,
        bank_name: str,
    ) -> None:
        """Background task: Create mandate and send auth instructions."""
        try:
            result = await self.mandate.create_mandate(
                phone_number=phone_number,
                mono_customer_id=mono_customer_id,
                account_id=account_id,
                account_number=account_number,
                bank_code=bank_code,
                bank_name=bank_name,
            )

            if result["success"]:
                mandate = result["mandate"]
                transfer_destinations = mandate.transfer_destinations or []
                await self.mandate.send_auth_instructions(
                    phone_number=phone_number,
                    account_number=account_number,
                    bank_name=bank_name,
                    transfer_destinations=transfer_destinations,
                )

                # Notify user of success
                msg = (
                    f"✓ Your {bank_name} account has been added! Complete the ₦50 verification transfer to activate it."
                )
                await self.mandate.enqueue_outbox_say(phone_number, msg)
            else:
                logger.error("mandate_creation_failed_for_add", error=result.get("error"), phone=phone_number)

        except Exception as e:
            import traceback

            logger.error(
                "mandate_setup_error",
                error=str(e),
                phone=phone_number,
                traceback=traceback.format_exc(),
            )
