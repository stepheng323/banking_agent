"""Shared service for beneficiary suggestions across all transaction types."""

import json
import traceback
from typing import Any

from apps.core.src.messaging.outbox import enqueue_outbox_say
from shared.cache.redis_client import RedisClient
from shared.i18n import render_message
from shared.queue.redis_queue import RedisQueue
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BeneficiarySuggestionService:
    """Suggest beneficiaries across transfer, airtime, and data flows."""

    def __init__(
        self,
        queue: RedisQueue,
    ):
        """
        Initialize beneficiary suggestion service.

        Args:
            queue: Redis queue for outbox intents
            redis_client: Redis client for storing suggestion context
        """
        self.queue = queue
        self.redis_client = queue._redis or RedisClient.get_client()

    async def check_and_suggest_beneficiary(
        self,
        phone_number: str,
        beneficiary_type: str,
        recipient_data: dict[str, Any],
        transaction_id: str | None = None,
        send_message: bool = True,
        channel: str = "whatsapp",
        locale: str = "en",
    ) -> str | None:
        """
        Check if recipient is new beneficiary and suggest saving.

        Args:
            phone_number: User's phone number
            beneficiary_type: Type of beneficiary ("transfer", "airtime", or "data")
            recipient_data: Dict with recipient information (varies by type)
            transaction_id: Optional transaction ID
            send_message: Whether to enqueue the message or return it.
            channel: Target channel for outbox delivery.

        Returns:
            str: The suggestion message if generated and send_message=False.
            None: If sent immediately or no suggestion needed.
        """
        try:
            async with UnitOfWork() as uow:
                if not uow.users or not uow.transactions:
                    return None

                user = await uow.users.get_by_phone(phone_number)
                if not user:
                    logger.debug("debug_user")
                    return None

                user_id = str(user.id)
                has_beneficiary_repo = bool(uow.beneficiaries)
                exists_in_beneficiaries = False

                if beneficiary_type == "transfer":
                    if recipient_data.get("is_self") or recipient_data.get("is_own_account"):
                        return None
                    account_number = recipient_data.get("account_number")
                    bank_code = recipient_data.get("bank_code")
                    recipient_name = recipient_data.get("name", "")

                    if not account_number or not bank_code:
                        return None

                    if has_beneficiary_repo:
                        try:
                            exists_in_beneficiaries = not await uow.beneficiaries.should_suggest_beneficiary(
                                user_id, account_number, bank_code, beneficiary_type="transfer"
                            )
                        except Exception:
                            exists_in_beneficiaries = False

                    if has_beneficiary_repo and not exists_in_beneficiaries:
                        if transaction_id:
                            transaction = await uow.transactions.get_by_id(str(transaction_id))
                            if transaction:
                                await uow.transactions.update(transaction, beneficiary_suggested=True)
                                await uow.commit()

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
                            message = render_message(
                                "beneficiary.suggestion.ask_save_transfer_with_alias",
                                locale,
                                {
                                    "recipient_display": recipient_display,
                                    "bank_display": bank_display,
                                    "masked_account": masked_acct,
                                    "alias": original_alias.title(),
                                },
                            )
                        else:
                            message = render_message(
                                "beneficiary.suggestion.ask_save_transfer_default",
                                locale,
                                {
                                    "recipient_display": recipient_display,
                                    "bank_display": bank_display,
                                    "masked_account": masked_acct,
                                },
                            )

                        if send_message:
                            await enqueue_outbox_say(
                                self.queue,
                                phone_number,
                                channel,
                                message,
                                metadata={"source": "beneficiary_suggestion"},
                            )
                            logger.info("beneficiary_suggestion_enqueued_for")
                            return None
                        return message

                elif beneficiary_type in ("airtime", "data"):
                    recipient_phone = recipient_data.get("phone", "")
                    network = recipient_data.get("network", "")
                    recipient_name = recipient_data.get("name", "")

                    if not recipient_phone or not network:
                        return None

                    if (
                        recipient_phone == phone_number
                        or (len(recipient_phone) >= 10 and phone_number.endswith(recipient_phone[-10:]))
                        or (len(phone_number) >= 10 and recipient_phone.endswith(phone_number[-10:]))
                    ):
                        return None

                    if has_beneficiary_repo:
                        try:
                            exists_in_beneficiaries = not await uow.beneficiaries.should_suggest_airtime_beneficiary(
                                user_id, recipient_phone, network
                            )
                        except Exception:
                            exists_in_beneficiaries = False

                    if has_beneficiary_repo and not exists_in_beneficiaries:
                        if transaction_id:
                            transaction = await uow.transactions.get_by_id(str(transaction_id))
                            if transaction:
                                await uow.transactions.update(transaction, beneficiary_suggested=True)
                                await uow.commit()

                        suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
                        masked_phone = f"…{recipient_phone[-4:]}" if len(recipient_phone) >= 4 else recipient_phone
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

                        message = render_message(
                            "beneficiary.suggestion.ask_save_phone_default",
                            locale,
                            {
                                "recipient_display": recipient_display,
                                "network": network,
                                "masked_phone": masked_phone,
                            },
                        )

                        if send_message:
                            await enqueue_outbox_say(
                                self.queue,
                                phone_number,
                                channel,
                                message,
                                metadata={"source": "beneficiary_suggestion"},
                            )
                            logger.info("beneficiary_suggestion_enqueued_for")
                            return None
                        return message
                else:
                    logger.warning("unknown_beneficiary")

        except Exception:
            logger.error("error_beneficiary")
            traceback.print_exc()

        return None

    async def save_beneficiary(self, phone_number: str, alias: str | None = None, locale: str = "en") -> str:
        """
        Save the pending beneficiary suggestion.

        Args:
            phone_number: User's phone number
            alias: Optional alias override

        Returns:
            Success or error message
        """
        suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
        data_json = await self.redis_client.get(suggestion_key)

        if not data_json:
            return render_message("beneficiary.suggestion.no_recent", locale)

        try:
            data = json.loads(data_json)
            beneficiary_type = data.get("beneficiary_type", "transfer")

            async with UnitOfWork() as uow:
                user = await uow.users.get_by_phone(phone_number)
                if not user:
                    return render_message("beneficiary.suggestion.user_not_found", locale)

                user_id = str(user.id)
                final_alias = (
                    alias
                    or data.get("alias_suggested")
                    or data.get("recipient_name")
                    or render_message("beneficiary.suggestion.default_alias", locale)
                )

                if beneficiary_type == "transfer":
                    await uow.beneficiaries.create(
                        user_id=user_id,
                        account_number=data["account_number"],
                        bank_code=data["bank_code"],
                        bank_name=data["bank_name"],
                        account_name=data.get("recipient_name", ""),
                        alias=final_alias,
                        beneficiary_type="transfer",
                    )
                elif beneficiary_type in ("airtime", "data"):
                    await uow.beneficiaries.create(
                        user_id=user_id,
                        account_number=data["phone_number"],
                        bank_name=data["network"],
                        account_name=data.get("recipient_name", data["phone_number"]),
                        alias=final_alias,
                        beneficiary_type="airtime",
                    )

                await uow.commit()

            # Clear the suggestion
            await self.redis_client.delete(suggestion_key)

            from shared.cache.user_data import UserDataCache
            await UserDataCache(redis_client=self.redis_client).invalidate_beneficiaries(phone_number)

            return render_message(
                "beneficiary.suggestion.saved",
                locale,
                {"alias": final_alias.upper()},
            )

        except Exception as e:
            logger.error("save_beneficiary_failed", error=str(e))
            return render_message("beneficiary.suggestion.save_failed", locale)
