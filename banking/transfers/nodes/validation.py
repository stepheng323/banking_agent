"""Validation logic for transfer flow."""

from typing import Any

from banking.presentation.formatters.accounts import format_accounts_list
from banking.presentation.formatters.currency import format_naira
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.shared.source_account_guard import find_account_by_id
from banking.transfers.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.transfers.pipeline.base import TransferStep
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _resolve_account_aware_amount(
    data: TransferPayload,
    context: TransferContext,
    worker_context: Any,
) -> TransactionResult | None:
    if not data.transfer_all and data.transfer_percentage is None:
        return None

    locale = context.language

    if not data.source_account_id:
        accounts_list = format_accounts_list(context.accounts, locale=locale)
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["source_account_id"],
            prompt=render_message("source_account.choose_prompt", locale, {"accounts_list": accounts_list}),
        )

    linked_accounts = context.all_accounts or context.accounts
    selected_account = find_account_by_id(linked_accounts, data.source_account_id) or find_account_by_id(
        context.accounts,
        data.source_account_id,
    )
    if not selected_account:
        logger.warning("account_aware_amount_source_not_found", source_account_id=data.source_account_id)
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("account.balance.unavailable", locale),
        )

    dd_provider = getattr(worker_context, "dd_provider", None)
    if dd_provider is None:
        logger.warning("account_aware_amount_provider_unavailable", source_account_id=data.source_account_id)
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("account.balance.unavailable", locale),
        )

    provider_account_id = selected_account.get("mono_account_id") or selected_account.get("account_id")
    if not provider_account_id:
        logger.warning("account_aware_amount_missing_provider_account_id", source_account_id=data.source_account_id)
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("account.balance.unavailable", locale),
        )

    try:
        balance = await dd_provider.get_balance(str(provider_account_id), real_time=True)
    except Exception as exc:
        logger.warning(
            "account_aware_amount_balance_lookup_failed", source_account_id=data.source_account_id, error=str(exc)
        )
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("account.balance.unavailable", locale),
        )

    if not getattr(balance, "success", False):
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("account.balance.unavailable", locale),
        )

    available_balance = max(0.0, float(getattr(balance, "available_balance", 0.0) or 0.0))
    if data.transfer_all:
        derived_amount = round(available_balance, 2)
    else:
        percentage = float(data.transfer_percentage or 0.0)
        derived_amount = round((available_balance * percentage) / 100.0, 2)

    if derived_amount <= 0:
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("transfer.funding.insufficient_funds", locale),
        )

    return TransactionResult(
        outcome=TransactionOutcome.OK,
        patch={
            "amount": derived_amount,
            "funding_plan": None,
        },
    )


class ValidationStep(TransferStep):
    """Validates amount and transfer details."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        del gates
        derived_amount_result = await _resolve_account_aware_amount(data, context, worker_context)
        if derived_amount_result is not None and derived_amount_result.outcome != TransactionOutcome.OK:
            return derived_amount_result

        derived_patch = (derived_amount_result.patch or {}) if derived_amount_result is not None else {}
        data_for_validation = data.model_copy(update=derived_patch) if derived_patch else data

        if (
            data_for_validation.amount is None
            and not data_for_validation.transfer_all
            and not data_for_validation.transfer_percentage
            and not data_for_validation.amount_suggestion_disabled
            and (data_for_validation.recipient_resolved_name or data_for_validation.recipient_name)
            and getattr(worker_context, "transaction_repo", None) is not None
            and getattr(worker_context, "user_id", None)
        ):
            recipient_hint = (
                data_for_validation.recipient_resolved_name or data_for_validation.recipient_name or ""
            ).strip()
            if recipient_hint:
                try:
                    recent = await worker_context.transaction_repo.get_recent_successful_transfer_by_recipient(
                        str(worker_context.user_id),
                        recipient_hint,
                    )
                    if recent and recent.amount:
                        suggested_amount = float(recent.amount)
                        suggested_amount_text = format_naira(suggested_amount)
                        return TransactionResult(
                            outcome=TransactionOutcome.NEEDS_INPUT,
                            required_fields=["amount"],
                            prompt=render_message(
                                "transfer.validation.ask_amount_with_suggestion",
                                context.language,
                                {
                                    "amount": suggested_amount_text,
                                    "recipient_name": recipient_hint,
                                },
                            ),
                            patch={"suggested_amount": suggested_amount},
                            details={
                                "option_context": "TRANSFER_AMOUNT_SUGGESTION",
                                "options": [
                                    {"id": "1", "title": f"Use {suggested_amount_text}"},
                                    {"id": "2", "title": "Enter a new amount"},
                                ],
                            },
                        )
                except Exception as exc:
                    logger.warning("suggested_amount_lookup_failed", error=str(exc))

        service = worker_context.validation_service
        validation_payload = (
            data_for_validation.model_copy(update={"transfer_all": False, "transfer_percentage": None})
            if derived_patch
            else data_for_validation
        )

        res_amount = service.validate_amount(validation_payload, context)
        if res_amount.outcome != TransactionOutcome.OK:
            return res_amount

        patch = dict(derived_patch)
        patch.update(res_amount.patch or {})

        data_for_val = (
            validation_payload.model_copy(update=res_amount.patch) if res_amount.patch else validation_payload
        )

        res_transfer = service.validate_transfer(data_for_val, context)
        if res_transfer.outcome != TransactionOutcome.OK:
            return res_transfer

        final_patch = patch
        if res_transfer.patch:
            final_patch.update(res_transfer.patch)

        return TransactionResult(outcome=TransactionOutcome.OK, patch=final_patch)
