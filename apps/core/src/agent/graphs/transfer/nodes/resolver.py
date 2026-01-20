"""Beneficiary resolution logic."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.graphs.transfer.models.types import TransferContext, TransferPayload
from apps.core.src.agent.orchestrator.models.domain import TransferOutcome, TransferResult
from shared.database.models import Beneficiary


async def resolve_beneficiary(
    payload: TransferPayload,
    ctx: TransferContext,
    banking_provider: Any | None = None,
    bank_cache: Any | None = None,
) -> TransferResult:
    """Resolve recipient name to bank details using BeneficiaryMatcher."""
    if payload.beneficiary_id:
        selected = next((b for b in ctx.beneficiaries if str(b.get("id")) == payload.beneficiary_id), None)
        if selected:
            return TransferResult(
                outcome=TransferOutcome.OK,
                patch={
                    "recipient_account": str(selected.get("account_number")),
                    "recipient_bank_code": str(selected.get("bank_code")),
                    "recipient_bank_name": selected.get("bank_name"),
                    "recipient_resolved_name": selected.get("account_name") or selected.get("alias"),
                    "recipient_name": selected.get("account_name") or selected.get("alias"),  # Update name too
                },
            )

    if payload.recipient_resolved_name:
        return TransferResult(outcome=TransferOutcome.OK)

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
        # else:
        #     print("DEBUG: Bank cache is None") # This line was removed, but the else block is now empty. Keeping it commented for clarity.

    if payload.recipient_account and payload.recipient_bank_code and not payload.recipient_resolved_name:
        if banking_provider:
            try:
                # Use resolve_account (dict return) instead of resolve_account_number (object return)
                print(f"DEBUG: Resolving {payload.recipient_account} with {payload.recipient_bank_code}")
                resolved = await banking_provider.resolve_account(
                    payload.recipient_account, payload.recipient_bank_code
                )
                print(f"DEBUG: Resolution Result: {resolved}")
                if resolved and resolved.get("success"):
                    return TransferResult(
                        outcome=TransferOutcome.OK,
                        patch={
                            "recipient_resolved_name": resolved.get("account_name"),
                            "recipient_name": resolved.get("account_name"),  # Ensure name is present
                            "recipient_bank_name": payload.recipient_bank_name,
                            "recipient_bank_code": payload.recipient_bank_code,
                        },
                    )
            except Exception:
                pass

        if not payload.recipient_name and not payload.recipient_resolved_name:
            # Logic to ask for name will trigger below if we don't return OK here
            pass

    if not payload.recipient_name:
        # 2b. If we have an account number, we tried resolution above and failed (or bank was missing)
        if payload.recipient_account:
            if not payload.recipient_bank_code and not payload.recipient_bank_name:
                return TransferResult(
                    outcome=TransferOutcome.NEEDS_INPUT,
                    required_fields=["recipient_bank_name"],
                    prompt=f"I have account {payload.recipient_account}, but I need the Bank Name.",
                )
            # If we have Bank Name but no Code -> Bank Lookup Failed
            if payload.recipient_bank_name and not payload.recipient_bank_code:
                return TransferResult(
                    outcome=TransferOutcome.NEEDS_INPUT,
                    required_fields=["recipient_bank_name"],
                    prompt=f"I couldn't find a bank named '{payload.recipient_bank_name}'. Could you verify the name?",
                )

            # If we have Code + Account but still no Name -> Resolution Failed (Network or Invalid Account)
            # We fail gracefully by asking for the name manually, or treating it as a failure?
            # Better to ask for name to allow manual override, but warn.
            return TransferResult(
                outcome=TransferOutcome.NEEDS_INPUT,
                required_fields=["recipient_name"],
                prompt=f"I couldn't verify account {payload.recipient_account} at {payload.recipient_bank_name}. Please provide the Recipient Name to proceed manually.",
            )

        return TransferResult(
            outcome=TransferOutcome.NEEDS_INPUT,
            required_fields=["recipient_name"],
            prompt="Who is the recipient?",
        )

    matcher = BeneficiaryMatcher()
    beneficiaries = [Beneficiary(**b) for b in ctx.beneficiaries]

    status, single, candidates = matcher.match(payload.recipient_name, beneficiaries)

    if status == "single" and single:
        return TransferResult(
            outcome=TransferOutcome.OK,
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
        return TransferResult(
            outcome=TransferOutcome.NEEDS_INPUT,
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
        return TransferResult(
            outcome=TransferOutcome.NEEDS_INPUT,
            required_fields=["recipient_account", "recipient_bank_name"],
            prompt=f"I couldn't find {payload.recipient_name}. Please provide {' and '.join(missing)}.",
        )

    return TransferResult(outcome=TransferOutcome.OK)
