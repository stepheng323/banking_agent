"""Prepare provider-specific recipient details for funded payouts."""

from typing import Any

from banking.presentation.formatters.recipient_prompt_names import sanitize_recipient_display_name
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transfers.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.transfers.pipeline.base import TransferStep
from banking.transfers.resolution.modes import (
    POOLED_MODE,
    provider_identity_is_authoritative,
    recipient_names_equivalent,
)
from shared.config.settings import settings
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


class PayoutPreparationStep(TransferStep):
    """Resolve funded-transfer payout recipient with the payout provider."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        del gates
        return await prepare_payout_recipient(data, context, worker_context)


def _is_multi_source_funding_plan(plan: dict[str, Any] | None) -> bool:
    return isinstance(plan, dict) and plan.get("is_single_source") is False


def _provider_name(provider: Any, default: str | None = None) -> str:
    return (
        str(getattr(provider, "provider_name", None) or default or settings.payout_resolver_provider_name)
        .strip()
        .lower()
    )


def _ask_account_and_bank_prompt(locale: str, recipient_name: str | None) -> str:
    display_name = sanitize_recipient_display_name(recipient_name, locale)
    return render_message(
        "response.templates.ask_account_number_and_bank",
        locale,
        {"recipient_name": display_name},
    )


async def prepare_payout_recipient(
    payload: TransferPayload,
    ctx: TransferContext,
    worker_context: Any = None,
) -> TransactionResult:
    """Remap multi-source transfer recipients to payout-provider bank codes."""
    if not _is_multi_source_funding_plan(payload.funding_plan):
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})

    locale = ctx.language
    resolver_provider = getattr(worker_context, "payout_resolver_provider", None) if worker_context else None
    bank_cache = getattr(worker_context, "payout_bank_cache", None) if worker_context else None
    if not resolver_provider or not bank_cache:
        logger.error("payout_preparation_missing_provider")
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("transfer.error.execution_failed", locale),
            retryable=True,
        )

    if not payload.recipient_account or not payload.recipient_bank_name:
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_account", "recipient_bank_name"],
            prompt=_ask_account_and_bank_prompt(locale, payload.recipient_name),
        )

    provider_name = _provider_name(resolver_provider)
    if (
        payload.recipient_bank_code
        and payload.recipient_resolved_name
        and payload.recipient_resolution_mode == POOLED_MODE
        and str(payload.recipient_bank_code_provider or "").strip().lower() == provider_name
    ):
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})

    try:
        await bank_cache.ensure_banks_cached(resolver_provider.get_banks)
        payout_bank_code = await bank_cache.get_bank_code(payload.recipient_bank_name)
        if not payout_bank_code:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_bank_name"],
                prompt=render_message(
                    "transfer.resolve.bank_name_not_found",
                    locale,
                    {"bank_name": payload.recipient_bank_name},
                ),
            )

        logger.info(
            "payout_recipient_resolution_started",
            provider=provider_name,
            recipient_account_hash=log_fingerprint(payload.recipient_account),
            recipient_bank_code=payout_bank_code,
        )
        resolved = await resolver_provider.resolve_account(payload.recipient_account, payout_bank_code)
        if not resolved or not resolved.success or not resolved.account:
            logger.warning(
                "payout_recipient_resolution_failed",
                provider=provider_name,
                recipient_account_hash=log_fingerprint(payload.recipient_account),
                recipient_bank_code=payout_bank_code,
                error=getattr(resolved, "error", None),
            )
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_account", "recipient_bank_name"],
                prompt=(
                    f"{render_message('response.templates.account_validation_failed', locale)} "
                    f"{_ask_account_and_bank_prompt(locale, payload.recipient_name)}"
                ),
            )

        verified_bank_code = resolved.account.bank_code or payout_bank_code
        verified_name = resolved.account.account_name
        identity_is_authoritative = provider_identity_is_authoritative(resolver_provider)
        existing_name = payload.recipient_resolved_name
        existing_provider = payload.recipient_resolution_provider
        resolved_name = verified_name
        resolution_provider: str | None = provider_name
        if not identity_is_authoritative and existing_name:
            resolved_name = existing_name
            resolution_provider = existing_provider

        if identity_is_authoritative and existing_name and not recipient_names_equivalent(existing_name, verified_name):
            logger.warning(
                "payout_recipient_identity_mismatch",
                provider=provider_name,
                recipient_account_hash=log_fingerprint(payload.recipient_account),
                recipient_bank_code=verified_bank_code,
            )

        patch = {
            "recipient_account": resolved.account.account_number or payload.recipient_account,
            "recipient_bank_code": verified_bank_code,
            "recipient_bank_code_provider": provider_name,
            "recipient_resolution_mode": POOLED_MODE,
            "recipient_resolved_name": resolved_name,
        }
        if resolution_provider:
            patch["recipient_resolution_provider"] = resolution_provider
        return TransactionResult(outcome=TransactionOutcome.OK, patch=patch)
    except Exception as exc:
        logger.error(
            "payout_preparation_failed",
            provider=provider_name,
            error=str(exc),
        )
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("transfer.error.execution_failed", locale),
            retryable=True,
        )
