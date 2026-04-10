"""Mandate management service for onboarding."""

import uuid as uuid_module
from datetime import timedelta
from typing import TYPE_CHECKING

from shared.cache.user_data import UserDataCache
from shared.clients.providers.mono import MonoApiError, mono_client
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.onboarding.mandate_messages import format_mandate_auth_message
from shared.utils.datetime import utc_now_naive
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from shared.services.delivery_service import DeliveryService

logger = get_logger(__name__)


class MandateService:
    """Handles mandate creation, reinitiation, and notifications."""

    def __init__(
        self,
        publisher: object | None = None,
        delivery_service: "DeliveryService | None" = None,
        queue: object | None = None,
    ) -> None:
        # publisher/queue are retained for backwards compatibility with older call sites.
        del publisher, queue
        self.delivery_service = delivery_service

    def _get_delivery_service(self) -> "DeliveryService":
        from shared.services.delivery_service import DeliveryService

        if self.delivery_service is None:
            self.delivery_service = DeliveryService()
        return self.delivery_service

    async def enqueue_outbox_say(self, phone_number: str, text: str, channel: str = "whatsapp") -> None:
        await self._get_delivery_service().deliver_text(
            phone_number=phone_number,
            channel=channel,
            text=text,
            metadata={"source": "mandate_service"},
        )

    def build_mandate_auth_message(
        self,
        account_number: str,
        bank_name: str,
        transfer_destinations: list,
        is_reinitiation: bool = False,
    ) -> str:
        """Build WhatsApp message with mandate authorization instructions."""
        normalized_destinations: list[dict[str, str]] = []
        for item in transfer_destinations:
            if isinstance(item, dict):
                bank_name_value = str(item.get("bank_name") or "").strip()
                account_number_value = str(item.get("account_number") or "").strip()
            else:
                bank_name_value = str(getattr(item, "bank_name", "") or "").strip()
                account_number_value = str(getattr(item, "account_number", "") or "").strip()
            if not bank_name_value or not account_number_value:
                continue
            normalized_destinations.append(
                {
                    "bank_name": bank_name_value,
                    "account_number": account_number_value,
                }
            )

        return format_mandate_auth_message(
            account_number=account_number,
            bank_name=bank_name,
            transfer_destinations=normalized_destinations,
            is_reinitiation=is_reinitiation,
        )

    async def create_mandate(
        self,
        phone_number: str,
        mono_customer_id: str,
        account_id: str,
        account_number: str,
        bank_code: str,
        bank_name: str,
    ) -> dict:
        """Create a new mandate for an account."""
        del bank_name
        mandate_reference = f"FP-{uuid_module.uuid4().hex[:12].upper()}"
        start_date = utc_now_naive().strftime("%Y-%m-%d")
        end_date = (utc_now_naive() + timedelta(days=365)).strftime("%Y-%m-%d")

        try:
            mandate = await mono_client.create_mandate(
                customer_id=mono_customer_id,
                account_number=account_number,
                bank_code=bank_code,
                amount=100000000,  # Max amount in kobo
                reference=mandate_reference,
                start_date=start_date,
                end_date=end_date,
            )
            logger.info("mandate_created", mandate_id=mandate.id, phone=phone_number)

            async with UnitOfWork() as uow:
                if uow.accounts:
                    db_account = await uow.accounts.get_by_account_id(account_id)
                    if db_account:
                        db_account.mandate_id = mandate.id
                        db_account.mandate_status = "pending"
                        transfer_destinations = mandate.transfer_destinations or []
                        existing_extra = db_account.extra_data or {}
                        db_account.extra_data = {
                            **existing_extra,
                            "mandate_created_at": utc_now_naive().isoformat(),
                            "transfer_destinations": [
                                {"bank_name": dest.bank_name, "account_number": dest.account_number}
                                for dest in transfer_destinations
                            ],
                        }

            try:
                cache = UserDataCache()
                await cache.invalidate_accounts(phone_number)
            except Exception:
                pass

            return {"success": True, "mandate": mandate}

        except MonoApiError as e:
            logger.error("mandate_creation_failed", error=str(e), phone=phone_number)
            return {"success": False, "error": str(e)}

    async def reinitiate_mandate(self, phone_number: str, account_id: str, channel: str = "whatsapp") -> dict:
        """
        Reinitiate mandate for an existing account.

        Used when mandate has expired (>1 hour) or was cancelled.
        """
        try:
            async with UnitOfWork() as uow:
                if not uow.users or not uow.accounts:
                    return {"success": False, "error": "Database not available"}

                user = await uow.users.get_by_phone(phone_number)
                if not user:
                    return {"success": False, "error": "User not found"}

                account = await uow.accounts.get_by_account_id(account_id)
                if not account:
                    return {"success": False, "error": "Account not found"}

                mono_customer_id = user.mono_customer_id
                account_number = account.account_number
                bank_code = account.bank_code
                bank_name = account.bank_name

            if not mono_customer_id:
                return {
                    "success": False,
                    "error": "Mono customer not found. Please contact support.",
                }

            mandate_reference = f"FP-{uuid_module.uuid4().hex[:12].upper()}"
            start_date = utc_now_naive().strftime("%Y-%m-%d")
            end_date = (utc_now_naive() + timedelta(days=365)).strftime("%Y-%m-%d")

            mandate = await mono_client.create_mandate(
                customer_id=mono_customer_id,
                account_number=account_number,
                bank_code=bank_code,
                amount=100000000,
                reference=mandate_reference,
                start_date=start_date,
                end_date=end_date,
            )
            logger.info(
                "mandate_reinitiated",
                mandate_id=mandate.id,
                phone=phone_number,
                account_id=account_id,
            )

            # Update account with new mandate info
            async with UnitOfWork() as uow:
                if uow.accounts:
                    db_account = await uow.accounts.get_by_account_id(account_id)
                    if db_account:
                        db_account.mandate_id = mandate.id
                        db_account.mandate_status = "pending"
                        transfer_destinations = mandate.transfer_destinations or []
                        existing_extra = db_account.extra_data or {}
                        db_account.extra_data = {
                            **existing_extra,
                            "mandate_created_at": utc_now_naive().isoformat(),
                            "transfer_destinations": [
                                {"bank_name": dest.bank_name, "account_number": dest.account_number}
                                for dest in transfer_destinations
                            ],
                        }

            try:
                cache = UserDataCache()
                await cache.invalidate_accounts(phone_number)
            except Exception:
                pass

            transfer_destinations = mandate.transfer_destinations or []
            auth_message = self.build_mandate_auth_message(
                account_number=account_number,
                bank_name=bank_name,
                transfer_destinations=transfer_destinations,
                is_reinitiation=True,
            )
            await self.enqueue_outbox_say(phone_number, auth_message, channel)

            return {
                "success": True,
                "data": {"mandate_id": mandate.id, "message": "Mandate reinitiated successfully"},
            }

        except MonoApiError as e:
            logger.error("reinitiate_mandate_mono_error", error=str(e), phone=phone_number)
            return {"success": False, "error": f"Failed to reinitiate mandate: {e}"}
        except Exception as e:
            logger.error("reinitiate_mandate_error", error=str(e), phone=phone_number)
            return {"success": False, "error": "Failed to reinitiate mandate. Please try again."}

    async def send_auth_instructions(
        self,
        phone_number: str,
        account_number: str,
        bank_name: str,
        transfer_destinations: list,
        channel: str = "whatsapp",
    ) -> None:
        """Send mandate authorization instructions via WhatsApp or Telegram."""
        auth_message = self.build_mandate_auth_message(
            account_number=account_number,
            bank_name=bank_name,
            transfer_destinations=transfer_destinations,
        )
        await self.enqueue_outbox_say(phone_number, auth_message, channel)
