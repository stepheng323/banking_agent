"""Beneficiary Worker.

Handles basic CRUD operations for beneficiaries using UnitOfWork.
"""

import re
import time
import unicodedata
from typing import Any

from banking.beneficiaries.formatter import BeneficiaryFormatter
from banking.beneficiaries.models import BeneficiaryIntent
from banking.persistence.unit_of_work import UnitOfWork
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from shared.types.conversation_sets import (
    BeneficiaryQueryContract,
    BulkMutationRequest,
    BulkMutationReviewSnapshot,
)
from shared.types.read import ReadRequest, ReadResult, normalize_read_request
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _normalized_match_text(value: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", plain.casefold()).strip()


def _matches_name_filter(beneficiary: Any, name_filter: str | None) -> bool:
    needle = _normalized_match_text(name_filter)
    if not needle:
        return True
    fields = (
        getattr(beneficiary, "alias", None),
        getattr(beneficiary, "account_name", None),
    )
    return any(needle in _normalized_match_text(value) for value in fields)


def _matches_bank_filter(beneficiary: Any, bank_name: str | None) -> bool:
    needle = _normalized_match_text(bank_name)
    if not needle:
        return True
    return needle in _normalized_match_text(getattr(beneficiary, "bank_name", None))


def _version_token(entity: Any) -> str | None:
    updated_at = getattr(entity, "updated_at", None)
    if updated_at is None:
        return None
    isoformat = getattr(updated_at, "isoformat", None)
    return str(isoformat() if callable(isoformat) else updated_at)


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
        context = {**context, "task_payload": payload}
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
        raw_payload = context.get("task_payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        read_request = normalize_read_request(payload)
        raw_contract = payload.get("beneficiary_contract")
        if read_request is None or not isinstance(raw_contract, dict):
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("beneficiary.error.process_failed", locale),
            )
        try:
            contract = BeneficiaryQueryContract.model_validate(raw_contract)
        except ValueError:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("beneficiary.error.process_failed", locale),
            )
        response_shape = read_request.response_shape
        async with UnitOfWork() as uow:
            beneficiaries = await uow.beneficiaries.get_by_user(user_id)
            selected_ids = {
                str(value)
                for value in payload.get("selected_entity_ids", [])
                if isinstance(value, str) and value
            }
            beneficiaries = [
                beneficiary
                for beneficiary in beneficiaries
                if _matches_name_filter(beneficiary, contract.entity_name)
                and _matches_bank_filter(beneficiary, contract.bank_name)
                and (
                    contract.beneficiary_type is None
                    or getattr(beneficiary, "beneficiary_type", None) == contract.beneficiary_type
                )
                and (not selected_ids or str(getattr(beneficiary, "id", "")) in selected_ids)
            ]
            total_count = len(beneficiaries)
            start = read_request.offset
            page = beneficiaries[start : start + read_request.page_size]
            read_result = ReadResult(
                request=read_request,
                total_count=total_count,
                returned_count=0 if response_shape.startswith("fact_") else len(page),
                has_next=start + read_request.page_size < total_count,
                has_previous=start > 0,
            )

            simple_list = [
                {
                    "id": str(b.id) if getattr(b, "id", None) is not None else None,
                    "version_token": _version_token(b),
                    "name": b.account_name,
                    "alias": b.alias,
                    "bank": b.bank_name,
                    "account": b.account_number,
                    "beneficiary_type": getattr(b, "beneficiary_type", None),
                }
                for b in page
            ]

            if response_shape == "fact_count":
                if read_request.entity_name:
                    response = render_message(
                        "beneficiary.list.filtered_count",
                        locale,
                        {"count": total_count, "filter": read_request.entity_name},
                    )
                elif total_count == 0:
                    response = render_message("beneficiary.list.count_zero", locale)
                elif total_count == 1:
                    response = render_message("beneficiary.list.count_one", locale)
                else:
                    response = render_message("beneficiary.list.count_many", locale, {"count": total_count})
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=response,
                    read_result=read_result,
                )

            if response_shape == "fact_bool":
                response = render_message(
                    "beneficiary.list.exists_yes" if total_count else "beneficiary.list.exists_no",
                    locale,
                    {"filter": read_request.entity_name or ""},
                )
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=response,
                    read_result=read_result,
                )

            if not page:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=render_message("beneficiary.list.empty", locale),
                    read_result=read_result,
                )

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=BeneficiaryFormatter.format_beneficiary_list(
                    page,
                    locale=locale,
                    name_filter=read_request.entity_name,
                    has_next=read_result.has_next,
                ),
                details={"viewed_beneficiaries": simple_list},
                read_result=read_result,
                patch={
                    "beneficiary_contract": contract.model_dump(mode="json", exclude_none=True),
                },
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
            bank_name = getattr(b, "bank_name", None)
            account_number = getattr(b, "account_number", None)
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
        raw_request = payload.get("bulk_mutation")
        try:
            request = (
                BulkMutationRequest.model_validate(raw_request)
                if isinstance(raw_request, dict)
                else None
            )
        except ValueError:
            request = None
        if request is None or request.domain != "beneficiary" or request.action != "delete":
            target = payload.get("target_alias") or payload.get("name") or payload.get("alias")
            if isinstance(target, str) and target.strip():
                async with UnitOfWork() as uow:
                    candidates = [
                        beneficiary
                        for beneficiary in await uow.beneficiaries.get_by_user(user_id)
                        if _matches_name_filter(beneficiary, target)
                    ]
                prompt = render_message("beneficiary.delete.select_candidate", locale, {"target": target})
                preview = self._beneficiary_preview_lines(candidates[:5])
                if preview:
                    prompt = f"{prompt}\n\n" + "\n".join(preview)
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["beneficiary_id"],
                    prompt=prompt,
                )
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["beneficiary_id"],
                prompt=render_message("beneficiary.delete.missing_target", locale),
                error=render_message("beneficiary.delete.missing_target", locale),
            )

        target_ids = [ref.entity_id for ref in request.targets]
        async with UnitOfWork() as uow:
            repo = uow.beneficiaries
            matches = await repo.get_by_ids_for_update(user_id, target_ids)
            by_id = {str(beneficiary.id): beneficiary for beneficiary in matches}
            stale = [
                ref
                for ref in request.targets
                if ref.entity_id not in by_id or _version_token(by_id[ref.entity_id]) != ref.version_token
            ]
            if stale:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("conversation_set.stale_selection", locale),
                )

            confirmation = payload.get("confirmation")
            confirmed = isinstance(confirmation, dict) and confirmation.get("confirmed") is True
            if not confirmed:
                snapshot = BulkMutationReviewSnapshot(
                    request=request,
                    created_turn_id=str(context.get("message_id") or "") or None,
                    created_at_ts=time.time(),
                )
                summary = render_message(
                    "beneficiary.delete.review",
                    locale,
                    {"count": len(request.targets), "items": "\n".join(ref.display_label for ref in request.targets)},
                )
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_CONFIRMATION,
                    confirmation_summary=summary,
                    confirmation_snapshot=snapshot.model_dump(mode="json", exclude_none=True),
                    patch={"bulk_mutation": request.model_dump(mode="json", exclude_none=True)},
                )

            for beneficiary in matches:
                await repo.delete(beneficiary)
            await uow.commit()

        phone_number = context.get("phone_number")
        if phone_number:
            from shared.cache.redis_client import RedisClient
            from shared.cache.user_data import UserDataCache

            await UserDataCache(redis_client=RedisClient.get_client()).invalidate_beneficiaries(phone_number)

        async with UnitOfWork() as refresh_uow:
            remaining = await refresh_uow.beneficiaries.get_by_user(user_id)
        refreshed_request = ReadRequest(subject="beneficiary", response_shape="surface_list")
        refreshed_contract = BeneficiaryQueryContract(operation="list", response_shape="surface_list")
        viewed = [
            {
                "id": str(beneficiary.id),
                "version_token": _version_token(beneficiary),
                "name": getattr(beneficiary, "account_name", None),
                "alias": getattr(beneficiary, "alias", None),
                "bank": getattr(beneficiary, "bank_name", None),
                "account": getattr(beneficiary, "account_number", None),
                "beneficiary_type": getattr(beneficiary, "beneficiary_type", None),
            }
            for beneficiary in remaining[:5]
        ]

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=render_message("beneficiary.delete.bulk_success", locale, {"count": len(matches)}),
            details={"viewed_beneficiaries": viewed},
            read_result=ReadResult(
                request=refreshed_request,
                total_count=len(remaining),
                returned_count=min(5, len(remaining)),
                has_next=len(remaining) > 5,
            ),
            patch={
                "bulk_mutation": None,
                "invalidate_conversation_set_domain": "beneficiary",
                "beneficiary_contract": refreshed_contract.model_dump(mode="json", exclude_none=True),
            },
        )

    async def _update_beneficiary(self, user_id: str, payload: dict, context: dict[str, Any]) -> TransactionResult:
        del user_id, payload
        locale = LocaleManager.normalize(context.get("language")).value
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("beneficiary.update.not_supported", locale),
        )
