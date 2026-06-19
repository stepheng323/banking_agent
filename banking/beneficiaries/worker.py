"""Beneficiary Worker.

Handles basic CRUD operations for beneficiaries using UnitOfWork.
"""

from typing import Any

from banking.beneficiaries.formatter import BeneficiaryFormatter
from banking.beneficiaries.models import BeneficiaryIntent
from banking.persistence.unit_of_work import UnitOfWork
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from shared.messaging.body_blocks import render_body_blocks_text
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BeneficiaryWorker:
    """Worker for beneficiary operations."""

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        """Run beneficiary operation."""
        del user_message, pin_verified
        locale = LocaleManager.normalize(context.get("language")).value
        if payload.get("response_shape") and not context.get("response_shape"):
            context = {**context, "response_shape": payload.get("response_shape")}
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

            if intent == BeneficiaryIntent.ADD:
                return await self._add_beneficiary(user_id, payload, context)

            if intent == BeneficiaryIntent.DELETE:
                return await self._delete_beneficiary(user_id, payload, context)

            if intent == BeneficiaryIntent.UPDATE:
                # Default to list if ambiguous but routed here.
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
        response_shape = str(context.get("response_shape") or "").strip().lower()
        async with UnitOfWork() as uow:
            beneficiaries = await uow.beneficiaries.get_by_user(user_id)

            simple_list = [
                {"name": b.account_name, "alias": b.alias, "bank": b.bank_name, "account": b.account_number}
                for b in beneficiaries
            ]

            if response_shape in {"fact_count", "fact_bool"}:
                count = len(beneficiaries)
                if count == 0:
                    response = render_message("beneficiary.list.count_zero", locale)
                elif count == 1:
                    response = render_message("beneficiary.list.count_one", locale)
                else:
                    response = render_message("beneficiary.list.count_many", locale, {"count": count})
                if beneficiaries:
                    blocks = BeneficiaryFormatter.format_count_preview_blocks(
                        beneficiaries,
                        response,
                        locale=locale,
                    )
                    response = response if blocks is None else render_body_blocks_text(blocks)
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=response,
                    details={"viewed_beneficiaries": simple_list} if simple_list else {},
                )

            if not beneficiaries:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=render_message("beneficiary.list.empty", locale),
                )

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=BeneficiaryFormatter.format_beneficiary_list(beneficiaries, locale=locale),
                details={"viewed_beneficiaries": simple_list},
            )
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("beneficiary.error.process_failed", locale),
        )

    @staticmethod
    def _beneficiary_preview_lines(beneficiaries: list[Any]) -> list[str]:
        lines: list[str] = []
        for b in beneficiaries:
            alias = b.alias or b.account_name
            account_name = b.account_name
            bank_name = b.bank_name
            account_number = b.account_number
            display = str(alias or account_name or "").strip()
            if (
                isinstance(alias, str)
                and isinstance(account_name, str)
                and alias.strip()
                and account_name.strip()
                and alias.lower() != account_name.lower()
            ):
                display = f"{alias} ({account_name})"
            detail_parts = []
            if bank_name:
                detail_parts.append(str(bank_name))
            if account_number:
                detail_parts.append(f"…{str(account_number)[-4:]}")
            details = f" - {' • '.join(detail_parts)}" if detail_parts else ""
            if display:
                lines.append(f"• {display}{details}")
        return lines

    async def _add_beneficiary(self, user_id: str, payload: dict, context: dict) -> TransactionResult:
        del user_id, payload
        locale = LocaleManager.normalize(context.get("language")).value
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("beneficiary.add.manual_disabled", locale),
        )

    async def _delete_beneficiary(self, user_id: str, payload: dict, context: dict[str, Any]) -> TransactionResult:
        locale = LocaleManager.normalize(context.get("language")).value
        target = payload.get("target_alias") or payload.get("name") or payload.get("alias")
        if not isinstance(target, str) or not target.strip():
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("beneficiary.delete.missing_target", locale),
            )

        target_lower = target.lower()
        async with UnitOfWork() as uow:
            all_bens = await uow.beneficiaries.get_by_user(user_id)
            match = None

            for b in all_bens:
                alias = b.alias
                account_name = b.account_name
                if (isinstance(alias, str) and alias.lower() == target_lower) or (
                    isinstance(account_name, str) and account_name.lower() == target_lower
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

            await uow.beneficiaries.delete(match)
            await uow.commit()

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
        del user_id, payload
        locale = LocaleManager.normalize(context.get("language")).value
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("beneficiary.update.not_supported", locale),
        )
