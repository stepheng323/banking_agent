"""Beneficiary Worker.

Handles basic CRUD operations for beneficiaries using UnitOfWork.
"""

from typing import Any

from apps.core.src.agent.graphs.beneficiary.models import BeneficiaryIntent
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import LocaleManager, render_message
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BeneficiaryWorker:
    """Worker for beneficiary operations."""

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> TransactionResult:
        """Run beneficiary operation."""
        locale = LocaleManager.normalize(context.get("language")).value
        try:
            intent = payload.get("intent")
            user_id = context.get("user_id") or payload.get("user_id")

            if not user_id:
                phone = context.get("phone_number")
                if phone:
                    async with UnitOfWork() as uow:
                        user = await uow.users.get_by_phone(phone)
                        if user:
                            user_id = str(user.id)

            if not user_id:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("beneficiary.error.user_identification_failed", locale),
                )

            if intent == BeneficiaryIntent.LIST or payload.get("list_intent"):
                return await self._list_beneficiaries(user_id, context)

            elif intent == BeneficiaryIntent.ADD:
                return await self._add_beneficiary(user_id, payload, context)

            elif intent == BeneficiaryIntent.DELETE:
                return await self._delete_beneficiary(user_id, payload, context)

            elif intent == BeneficiaryIntent.UPDATE:
                # Default to list if ambiguous but routed here
                return await self._list_beneficiaries(user_id, context)

            return await self._list_beneficiaries(user_id, context)

        except Exception as e:
            logger.error("beneficiary_worker_error", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("beneficiary.error.process_failed", locale),
            )

    async def _list_beneficiaries(self, user_id: str, context: dict[str, Any]) -> TransactionResult:
        locale = LocaleManager.normalize(context.get("language")).value
        async with UnitOfWork() as uow:
            beneficiaries = await uow.beneficiaries.get_all_for_user(user_id)

            # Format for context
            simple_list = [
                {"name": b.account_name, "alias": b.alias, "bank": b.bank_name, "account": b.account_number}
                for b in beneficiaries
            ]

            if not beneficiaries:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=render_message("beneficiary.list.empty", locale),
                )

            lines = [render_message("beneficiary.list.header", locale), ""]
            for b in beneficiaries:
                alias = b.alias or b.account_name
                account_name = b.account_name

                if alias and account_name and alias.lower() != account_name.lower():
                    name_line = f"*{alias}* ({account_name})"
                else:
                    name_line = f"*{alias}*"

                detail_parts = []
                if b.bank_name:
                    detail_parts.append(b.bank_name)
                if b.account_number:
                    masked = f"…{b.account_number[-4:]}"
                    detail_parts.append(masked)

                details_line = "  " + " • ".join(detail_parts)

                lines.append(name_line)
                lines.append(details_line)
                lines.append("")

            return TransactionResult(
                outcome=TransactionOutcome.OK, response="\n".join(lines), details={"viewed_beneficiaries": simple_list}
            )

    async def _add_beneficiary(self, user_id: str, payload: dict, context: dict) -> TransactionResult:
        locale = LocaleManager.normalize(context.get("language")).value
        name = payload.get("name") or payload.get("account_name")
        alias = payload.get("alias")
        account_number = payload.get("account_number")
        bank_code = payload.get("bank_code")
        bank_name = payload.get("bank_name")

        if not account_number or (not bank_code and not bank_name):
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("beneficiary.add.missing_account_or_bank", locale),
            )

        provider = context.get("resolver_provider")
        resolved_name = None

        if provider:
            if not bank_code and bank_name:
                try:
                    banks_resp = await provider.get_banks()
                    if banks_resp.success:
                        target = bank_name.lower()
                        for bank in banks_resp.banks:
                            if bank.name.lower() == target or target in bank.name.lower():
                                bank_code = bank.code
                                bank_name = bank.name
                                break
                except Exception:
                    logger.warning("bank_resolution_failed")

            if account_number and bank_code:
                try:
                    resolved = await provider.resolve_account(account_number, bank_code)
                    if resolved.success and resolved.account:
                        resolved_name = resolved.account.account_name
                        if resolved.account.bank_code:
                            bank_code = resolved.account.bank_code
                except Exception as e:
                    logger.warning("account_resolution_failed", error=str(e))
                    return TransactionResult(
                        outcome=TransactionOutcome.FAILED,
                        error=render_message(
                            "beneficiary.add.account_verification_failed",
                            locale,
                            {"account_number": account_number},
                        ),
                    )

            if not resolved_name and provider:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message(
                        "beneficiary.add.account_lookup_failed",
                        locale,
                        {
                            "account_number": account_number,
                            "bank_name": bank_name or "",
                        },
                    ),
                )
        final_account_name = resolved_name or name or ""

        async with UnitOfWork() as uow:
            uow.beneficiaries.create(
                user_id=user_id,
                account_number=account_number,
                bank_code=bank_code or "",  # Fallback
                bank_name=bank_name or render_message("beneficiary.common.bank_unknown", locale),
                account_name=final_account_name,
                alias=alias
                or name
                or final_account_name
                or render_message("beneficiary.suggestion.default_alias", locale),
                beneficiary_type="transfer",
            )
            uow.commit()

        phone_number = context.get("phone_number")
        if phone_number:
            from shared.cache.redis_client import RedisClient
            from shared.cache.user_data import UserDataCache

            await UserDataCache(redis_client=RedisClient.get_client()).invalidate_beneficiaries(phone_number)

        display_name = alias or final_account_name or render_message("beneficiary.common.default_name", locale)
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=render_message(
                "beneficiary.add.success",
                locale,
                {
                    "display_name": display_name,
                    "account_name": final_account_name,
                },
            ),
        )

    async def _delete_beneficiary(self, user_id: str, payload: dict, context: dict[str, Any]) -> TransactionResult:
        locale = LocaleManager.normalize(context.get("language")).value
        target = payload.get("target_alias") or payload.get("name") or payload.get("alias")
        if not target:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("beneficiary.delete.missing_target", locale),
            )

        async with UnitOfWork() as uow:
            all_bens = await uow.beneficiaries.get_all_for_user(user_id)
            match = None

            for b in all_bens:
                if (b.alias and b.alias.lower() == target.lower()) or (
                    b.account_name and b.account_name.lower() == target.lower()
                ):
                    match = b
                    break

            if not match:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message(
                        "beneficiary.delete.not_found",
                        locale,
                        {"target": target},
                    ),
                )

            uow.beneficiaries.delete(match.id)
            uow.commit()

        phone_number = context.get("phone_number")
        if phone_number:
            from shared.cache.redis_client import RedisClient
            from shared.cache.user_data import UserDataCache

            await UserDataCache(redis_client=RedisClient.get_client()).invalidate_beneficiaries(phone_number)

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=render_message("beneficiary.delete.success", locale, {"target": target}),
        )

    async def _update_beneficiary(self, user_id: str, payload: dict, context: dict[str, Any]) -> TransactionResult:
        # Placeholder for update logic
        locale = LocaleManager.normalize(context.get("language")).value
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("beneficiary.update.not_supported", locale),
        )
