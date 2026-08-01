"""Transfer recipient resolution pipeline stage."""

from typing import Any

from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transfers.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.transfers.pipeline.base import TransferStep
from banking.transfers.resolution.modes import (
    POOLED_MODE,
    SINGLE_SOURCE_MODE,
    desired_recipient_resolution_mode,
    provider_identity_is_authoritative,
)
from banking.transfers.resolution.resolver import resolve_beneficiary


def _has_destination(payload: TransferPayload) -> bool:
    return bool(payload.recipient_account and (payload.recipient_bank_name or payload.recipient_bank_code))


def _has_resolution(payload: TransferPayload) -> bool:
    return bool(payload.recipient_resolved_name or payload.recipient_resolution_provider)


def _clear_resolution_for_mode_switch(payload: TransferPayload) -> TransferPayload:
    """Keep destination fields, but force account-name and provider-code verification."""
    return payload.model_copy(
        update={
            "recipient_bank_code": None,
            "recipient_bank_code_provider": None,
            "recipient_resolution_provider": None,
            "recipient_resolution_mode": None,
            "recipient_resolved_name": None,
            "name_mismatch": False,
            "name_match_score": None,
            "name_mismatch_warning": None,
        }
    )


class ResolutionStep(TransferStep):
    """Resolves beneficiary details."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        del gates
        desired_mode = desired_recipient_resolution_mode(data)
        resolver_provider = worker_context.resolver_provider
        bank_cache = worker_context.bank_cache

        payout_provider_is_authoritative = provider_identity_is_authoritative(
            getattr(worker_context, "payout_resolver_provider", None)
        )
        if desired_mode == POOLED_MODE and payout_provider_is_authoritative:
            resolver_provider = worker_context.payout_resolver_provider
            bank_cache = worker_context.payout_bank_cache

        resolution_input = data
        mode_switch_requires_resolution = bool(
            data.recipient_resolution_mode and data.recipient_resolution_mode != desired_mode
        ) or bool(
            desired_mode == POOLED_MODE
            and payout_provider_is_authoritative
            and data.recipient_resolution_mode != POOLED_MODE
        )
        if mode_switch_requires_resolution and _has_destination(data) and _has_resolution(data):
            resolution_input = _clear_resolution_for_mode_switch(data)

        result = await resolve_beneficiary(
            resolution_input,
            context,
            resolver_provider,
            bank_cache,
        )
        if result.outcome != TransactionOutcome.OK:
            return result

        patch = dict(result.patch or {})
        has_resolved_patch = bool(patch.get("recipient_resolved_name") or resolution_input.recipient_resolved_name)
        if desired_mode == SINGLE_SOURCE_MODE and has_resolved_patch:
            patch.setdefault("recipient_resolution_mode", SINGLE_SOURCE_MODE)
        elif (
            desired_mode == POOLED_MODE
            and resolver_provider is getattr(worker_context, "payout_resolver_provider", None)
            and has_resolved_patch
        ):
            patch.setdefault("recipient_resolution_mode", POOLED_MODE)
        if patch:
            result.patch = patch
        return result
