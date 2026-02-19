"""Beneficiary resolution logic."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.database.models import Beneficiary
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ResolutionStep(TransferStep):
    """Resolves beneficiary details."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any,
    ) -> TransactionResult:
        return await resolve_beneficiary(
            data,
            context,
            worker_context.banking_provider,
            worker_context.bank_cache,
        )


async def resolve_beneficiary(
    payload: TransferPayload,
    ctx: TransferContext,
    banking_provider: Any | None = None,
    bank_cache: Any | None = None,
) -> TransactionResult:
    """Resolve recipient name to bank details using BeneficiaryMatcher."""
    if payload.beneficiary_id:
        selected = next((b for b in ctx.beneficiaries if str(b.get("id")) == payload.beneficiary_id), None)
        if selected:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch={
                    "recipient_account": str(selected.get("account_number")),
                    "recipient_bank_code": str(selected.get("bank_code")),
                    "recipient_bank_name": selected.get("bank_name"),
                    "recipient_resolved_name": selected.get("account_name") or selected.get("alias"),
                    "recipient_name": selected.get("account_name") or selected.get("alias"),  # Update name too
                },
            )

    if payload.recipient_resolved_name:
        return TransactionResult(outcome=TransactionOutcome.OK)

    # 1b. Resolve Bank Code if missing
    if payload.recipient_bank_name and not payload.recipient_bank_code:
        if bank_cache:
            # Ensure banks are loaded in cache
            if banking_provider:
                await bank_cache.ensure_banks_cached(banking_provider.get_banks)

            code = await bank_cache.get_bank_code(payload.recipient_bank_name)
            if code:
                # Update payload directly as we are about to use it for account resolution
                payload.recipient_bank_code = code

    if payload.recipient_account and payload.recipient_bank_code and not payload.recipient_resolved_name:
        if banking_provider:
            try:
                # Use resolve_account (dict return) instead of resolve_account_number (object return)
                logger.info(
                    "resolving_recipient_account",
                    account=payload.recipient_account,
                    bank_code=payload.recipient_bank_code,
                )
                resolved = await banking_provider.resolve_account(
                    payload.recipient_account, payload.recipient_bank_code
                )
                if resolved and resolved.get("success"):
                    return TransactionResult(
                        outcome=TransactionOutcome.OK,
                        patch={
                            "recipient_resolved_name": resolved.get("account_name"),
                            "recipient_name": resolved.get("account_name"),  # Ensure name is present
                            "recipient_bank_name": payload.recipient_bank_name,
                            "recipient_bank_code": payload.recipient_bank_code,
                        },
                    )
            except Exception as e:
                logger.warning(
                    "recipient_account_resolution_failed",
                    error=str(e),
                    account=payload.recipient_account,
                    bank_code=payload.recipient_bank_code,
                )

        if not payload.recipient_name and not payload.recipient_resolved_name:
            # Logic to ask for name will trigger below if we don't return OK here
            pass

    if not payload.recipient_name:
        # 2b. If we have an account number, we tried resolution above and failed (or bank was missing)
        if payload.recipient_account:
            if not payload.recipient_bank_code and not payload.recipient_bank_name:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["recipient_bank_name"],
                    prompt=f"I have account {payload.recipient_account}, but I need the Bank Name.",
                )
            # If we have Bank Name but no Code -> Bank Lookup Failed
            if payload.recipient_bank_name and not payload.recipient_bank_code:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["recipient_bank_name"],
                    prompt=f"I couldn't find a bank named '{payload.recipient_bank_name}'. Could you verify the name?",
                )

            # If we have Code + Account but still no Name -> Resolution Failed (Network or Invalid Account)
            # We fail gracefully by asking for the name manually, or treating it as a failure?
            # Better to ask for name to allow manual override, but warn.
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_name"],
                prompt=f"I couldn't verify account {payload.recipient_account} at {payload.recipient_bank_name}. Please provide the Recipient Name to proceed manually.",
            )

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_name"],
            prompt="Who is the recipient?",
        )

    bank_term = (payload.recipient_bank_name or "").lower()

    own_accounts = ctx.accounts or []
    candidate_account = None

    if payload.is_self:
        if bank_term:
            candidate_account = next(
                (a for a in own_accounts if bank_term in (a.get("bank_name") or "").lower()),
                None,
            )
        elif payload.is_self and len(own_accounts) == 2:
            # "Send to myself" (no bank specified) - Smart Inference
            # If we know the source, the recipient MUST be the other account
            source_id = payload.source_account_id

            # If source ID missing, try resolving from bank name
            if not source_id and payload.source_bank_name:
                src_bank = payload.source_bank_name.lower()
                src_match = next(
                    (a for a in own_accounts if src_bank in (a.get("bank_name") or "").lower()),
                    None,
                )
                if src_match:
                    source_id = str(src_match.get("id"))

            if source_id:
                candidate_account = next(
                    (a for a in own_accounts if str(a.get("id")) != source_id),
                    None,
                )

    if candidate_account:
        # Confirm intent: User likely meant this account if they specified the bank
        # and didn't provide an external account number
        if not payload.recipient_account:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch={
                    "recipient_account": str(candidate_account.get("account_number")),
                    "recipient_bank_code": str(candidate_account.get("bank_code")),
                    "recipient_bank_name": candidate_account.get("bank_name"),
                    "recipient_resolved_name": f"My {candidate_account.get('bank_name')} Account",
                    "recipient_name": f"My {candidate_account.get('bank_name')}",
                    "is_self": True,
                },
            )

    matcher = BeneficiaryMatcher()
    beneficiaries = [Beneficiary(**b) for b in ctx.beneficiaries]

    status, single, candidates = matcher.match(payload.recipient_name, beneficiaries)

    if status == "single" and single:
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "recipient_account": str(single.account_number),
                "recipient_bank_code": str(single.bank_code),
                "recipient_bank_name": single.bank_name,
                "recipient_resolved_name": single.account_name or single.alias or payload.recipient_name,
            },
        )
    elif status == "clarify" and candidates:
        candidate_list = [
            {"id": str(b.id), "label": f"{b.account_name or b.alias} • {b.bank_name} • **{str(b.account_number)[-4:]}"}
            for b in candidates
        ]
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["beneficiary_id"],
            prompt="Which recipient did you mean?",
            details={
                "ambiguity": "MULTIPLE_BENEFICIARIES",
                "candidates": candidate_list,
            },
        )

    missing = []
    if not payload.recipient_account:
        missing.append("account number")
    if not payload.recipient_bank_name and not payload.recipient_bank_code:
        missing.append("bank name")

    if missing:
        missing_str = " and ".join(missing)

        # [UX] Conversational Prompt
        # Acknowledge what we know (Recipient + Amount) before asking for what's missing.
        base = f"I'm ready to send money to {payload.recipient_name}"
        if payload.amount:
            amt = payload.amount
            if isinstance(amt, (int, float)):
                amt = f"₦{amt:,.2f}".replace(".00", "")
            base = f"I can send {amt} to {payload.recipient_name}"

        prompt = f"{base}, but I need their {missing_str}. Please provide the account details."

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_account", "recipient_bank_name"],
            prompt=prompt,
        )

    return TransactionResult(outcome=TransactionOutcome.OK)
