"""Shared service for beneficiary suggestions across all transaction types."""

import asyncio
import json
import traceback
from typing import Any

from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BeneficiarySuggestionService:
    """Suggest beneficiaries across transfer, airtime, and data flows."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        redis_client=None,
    ):
        """
        Initialize beneficiary suggestion service.

        Args:
            whatsapp_client: WhatsApp client for sending notifications
            redis_client: Redis client for storing suggestion context
        """
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client or RedisClient.get_client()

    async def check_and_suggest_beneficiary(
        self,
        phone_number: str,
        beneficiary_type: str,
        recipient_data: dict[str, Any],
        transaction_id: str | None = None,
    ) -> None:
        """
        Check if recipient is new beneficiary and suggest saving.

        Args:
            phone_number: User's phone number
            beneficiary_type: Type of beneficiary ("transfer", "airtime", or "data")
            recipient_data: Dict with recipient information (varies by type)
            transaction_id: Optional transaction ID
        """
        try:
            with UnitOfWork() as uow:
                if not uow.users or not uow.transactions:
                    return

                user = uow.users.get_by_phone(phone_number)
                if not user:
                    logger.debug("debug_user")
                    return

                user_id = str(user.id)
                has_beneficiary_repo = bool(uow.beneficiaries)
                exists_in_beneficiaries = False

                if beneficiary_type == "transfer":
                    if recipient_data.get("is_self") or recipient_data.get("is_own_account"):
                        return
                    account_number = recipient_data.get("account_number")
                    bank_code = recipient_data.get("bank_code")
                    recipient_name = recipient_data.get("name", "")

                    if not account_number or not bank_code:
                        return

                    if has_beneficiary_repo:
                        try:
                            exists_in_beneficiaries = (
                                not uow.beneficiaries.should_suggest_beneficiary(
                                    user_id, account_number, bank_code, beneficiary_type="transfer"
                                )
                            )
                        except Exception:
                            exists_in_beneficiaries = False

                    if has_beneficiary_repo and not exists_in_beneficiaries:
                        if transaction_id:
                            transaction = uow.transactions.get_by_id(str(transaction_id))
                            if transaction:
                                uow.transactions.update(transaction, beneficiary_suggested=True)
                                uow.commit()

                        suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
                        masked_acct = f"…{str(account_number)[-4:]}"
                        recipient_display = recipient_name or masked_acct
                        bank_display = recipient_data.get("bank_name", "") or bank_code

                        original_alias = recipient_data.get("original_alias", "")

                        suggestion_context = {
                            "beneficiary_type": "transfer",
                            "transaction_id": transaction_id,
                            "recipient_name": recipient_name,
                            "account_number": account_number,
                            "bank_code": bank_code,
                            "bank_name": recipient_data.get("bank_name", ""),
                            "alias_suggested": original_alias or recipient_name or "",
                            "original_alias": original_alias,
                        }
                        await self.redis_client.set(
                            suggestion_key,
                            json.dumps(suggestion_context),
                            ex=3600,
                        )

                        if original_alias and original_alias.lower() != recipient_name.lower():
                            message = (
                                f"Would you like to save {recipient_display} "
                                f"({bank_display} • {masked_acct}) as a beneficiary?\n"
                                f"- Reply 'yes' to save as '{original_alias.title()}'\n"
                                f"- Or send a different name"
                            )
                        else:
                            message = (
                                f"Would you like to save {recipient_display} "
                                f"({bank_display} • {masked_acct}) as a beneficiary?\n"
                                f"- Reply 'yes' to save\n"
                                f"- Or send a name (e.g., 'Mum') to save with that alias"
                            )
                        asyncio.create_task(
                            self.whatsapp_client.send_text(to=phone_number, text=message)
                        )
                        logger.info("beneficiary_suggestion_sent_for")

                elif beneficiary_type in ("airtime", "data"):
                    recipient_phone = recipient_data.get("phone", "")
                    network = recipient_data.get("network", "")
                    recipient_name = recipient_data.get("name", "")

                    if not recipient_phone or not network:
                        return

                    if recipient_phone == phone_number or \
                       (len(recipient_phone) >= 10 and phone_number.endswith(recipient_phone[-10:])) or \
                       (len(phone_number) >= 10 and recipient_phone.endswith(phone_number[-10:])):
                        return

                    if has_beneficiary_repo:
                        try:
                            exists_in_beneficiaries = (
                                not uow.beneficiaries.should_suggest_airtime_beneficiary(
                                    user_id, recipient_phone, network
                                )
                            )
                        except Exception:
                            exists_in_beneficiaries = False

                    if has_beneficiary_repo and not exists_in_beneficiaries:
                        if transaction_id:
                            transaction = uow.transactions.get_by_id(str(transaction_id))
                            if transaction:
                                uow.transactions.update(transaction, beneficiary_suggested=True)
                                uow.commit()

                        suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
                        masked_phone = (
                            f"…{recipient_phone[-4:]}"
                            if len(recipient_phone) >= 4
                            else recipient_phone
                        )
                        recipient_display = recipient_name or masked_phone

                        suggestion_context = {
                            "beneficiary_type": beneficiary_type,
                            "transaction_id": transaction_id,
                            "recipient_name": recipient_name,
                            "phone_number": recipient_phone,
                            "network": network,
                            "alias_suggested": recipient_name or "",
                        }
                        await self.redis_client.set(
                            suggestion_key,
                            json.dumps(suggestion_context),
                            ex=3600,
                        )

                        message = (
                            f"Would you like to save {recipient_display} "
                            f"({network} • {masked_phone}) as a beneficiary?\n"
                            f"- Reply 'yes' to save\n"
                            f"- Or send a name (e.g., 'Mum') to save with that alias"
                        )
                        asyncio.create_task(
                            self.whatsapp_client.send_text(to=phone_number, text=message)
                        )
                        logger.info("beneficiary_suggestion_sent_for")
                else:
                    logger.warning("unknown_beneficiary")

        except Exception:
            logger.error("error_beneficiary")
            traceback.print_exc()
