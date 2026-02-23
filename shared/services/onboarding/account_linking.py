"""Account linking service for onboarding."""

import asyncio

from shared.clients.providers.mono import mono_client
from shared.models import CreateAccount, UserUpdate
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils import hash_plaintext, is_valid_pin_format
from shared.utils.logging import get_logger

from .mandate import MandateService
from .session import OnboardingStep, SessionManager

logger = get_logger(__name__)


class AccountLinkingService:
    """Handles account selection and onboarding completion."""

    def __init__(self, session_manager: SessionManager, mandate_service: MandateService):
        self.session = session_manager
        self.mandate = mandate_service

    async def select_account(self, flow_token: str, account_id: str | None) -> dict:
        """Store selected account."""
        if not account_id:
            session = await self.session.get_session(flow_token)
            return {
                "success": False,
                "error": "Please select an account.",
                "data": {"accounts": session.get("accounts", []) if session else []},
            }

        session = await self.session.get_session(flow_token)
        if not session:
            return {"success": False, "error": "Session expired. Please start over."}

        await self.session.update_session(
            flow_token,
            {
                "selected_account": account_id,
                "step": OnboardingStep.PIN_ENTRY.value,
            },
        )

        return {"success": True, "data": {"bvn": session.get("bvn")}}

    async def complete_onboarding(
        self,
        flow_token: str,
        pin: str | None,
        email: str | None,
        address: str | None,
        channel: str = "whatsapp",
    ) -> dict:
        """Complete onboarding by creating customer and linking account."""
        if not pin or not is_valid_pin_format(pin):
            return {"success": False, "error": "Invalid PIN. Please enter a 4 or 6-digit PIN."}

        if not email:
            return {"success": False, "error": "Email address is required."}

        if not address:
            return {"success": False, "error": "Address is required."}

        session = await self.session.get_session(flow_token)
        if not session:
            return {"success": False, "error": "Session expired. Please start over."}

        phone_number = session.get("phone_number") or flow_token.split("-")[-1]
        if not phone_number:
            return {"success": False, "error": "Phone number missing."}

        selected_account = None
        accounts = session.get("accounts", [])
        selected_account_id = session.get("selected_account")
        if accounts and selected_account_id:
            for acc in accounts:
                if acc["id"] == selected_account_id:
                    selected_account = acc
                    break

        if not selected_account:
            return {"success": False, "error": "No account selected."}

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
            async with UnitOfWork() as uow:
                if not uow.users or not uow.accounts:
                    return {"success": False, "error": "Database error."}

                user = await uow.users.get_by_phone(phone_number)
                if not user:
                    # Brand new user (e.g. Telegram onboarding) — register them now
                    from shared.repositories.user_repository import UserCreate

                    user = await uow.users.register_user(UserCreate(phone_number=phone_number))

                await uow.users.update_user(
                    str(user.id),
                    UserUpdate(
                        full_name=account_name,
                        email=email,
                        address=address,
                        transaction_pin=hashed_pin,
                        onboarding_status="onboarding_completed",
                        extra_data={"bvn": session.get("bvn")},
                    ),
                )

                existing_account = await uow.accounts.get_by_account_id(selected_account["id"])
                if not existing_account or getattr(existing_account, "user_id", None) != str(user.id):
                    await uow.accounts.create_account(
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

                # If this is a Telegram onboarding, link the chat_id identity
                if flow_token.startswith("onboarding-"):
                    chat_id = flow_token.split("-", 1)[1]
                    await uow.users.link_channel_identity(str(user.id), "telegram", chat_id)

            await self.session.update_session(flow_token, {"step": OnboardingStep.COMPLETE.value})

            asyncio.create_task(
                self._setup_mono_customer_and_mandate(
                    phone_number=phone_number,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    address=address,
                    bvn=session.get("bvn") or "",
                    account_id=selected_account["id"],
                    account_number=selected_account.get("account_number", ""),
                    bank_code=bank_code,
                    bank_name=selected_account.get("bank_name", ""),
                    channel=channel,
                )
            )

            return {
                "success": True,
                "data": {
                    "phone_number": phone_number,
                    "bvn": session.get("bvn"),
                    "account": selected_account,
                },
            }

        except Exception as e:
            logger.error("onboarding_complete_error", error=str(e))
            return {"success": False, "error": "Failed to complete onboarding. Please try again."}

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
        channel: str = "whatsapp",
    ) -> None:
        """Background task: Create Mono customer and mandate, then notify user."""
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

            async with UnitOfWork() as uow:
                if uow.users:
                    user = await uow.users.get_by_phone(phone_number)
                    if user:
                        await uow.users.update_user(str(user.id), UserUpdate(mono_customer_id=customer.id))

            result = await self.mandate.create_mandate(
                phone_number=phone_number,
                mono_customer_id=customer.id,
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
                    channel=channel,
                )

        except Exception as e:
            import traceback

            logger.error(
                "mono_setup_background_error",
                error=str(e),
                phone=phone_number,
                traceback=traceback.format_exc(),
            )
            try:
                error_msg = (
                    "⚠️ We encountered an issue setting up your account. "
                    "Our team has been notified. Please try again later or contact support."
                )
                await self.mandate.enqueue_outbox_say(phone_number, error_msg, channel)
            except Exception:
                pass
