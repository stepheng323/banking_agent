"""Source account selection logic."""

from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.formatters.accounts import format_accounts_list


class SourceSelectionStep(TransferStep):
    """Selects source account."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any,
    ) -> TransactionResult:
        return await select_source_account(data, context)


async def select_source_account(
    payload: TransferPayload,
    ctx: TransferContext,
) -> TransactionResult:
    """Select source account if not provided."""
    if payload.source_account_id:
        if not payload.source_account_name:
            acc = next((a for a in ctx.accounts if str(a.get("id")) == payload.source_account_id), None)
            if acc:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch={"source_account_name": acc.get("account_name")},
                )
        return TransactionResult(outcome=TransactionOutcome.OK)

    accounts = ctx.accounts
    if not accounts:
        return TransactionResult(outcome=TransactionOutcome.FAILED, error="No accounts available.")

    if len(accounts) == 1:
        acc = accounts[0]
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "source_account_id": str(acc.get("id")),
                "source_bank_name": acc.get("bank_name"),
                "source_account_name": acc.get("account_name"),
                "source_account_number": acc.get("account_number"),
            },
        )

    default = next((a for a in accounts if a.get("is_default")), None)
    if default:
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "source_account_id": str(default.get("id")),
                "source_bank_name": default.get("bank_name"),
                "source_account_name": default.get("account_name"),
                "source_account_number": default.get("account_number"),
            },
        )

    if payload.source_account_index is not None and not payload.source_account_id:
        index = payload.source_account_index - 1
        if 0 <= index < len(accounts):
            acc = accounts[index]
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch={
                    "source_account_id": str(acc.get("id")),
                    "source_bank_name": acc.get("bank_name"),
                    "source_account_name": acc.get("account_name"),
                    "source_account_number": acc.get("account_number"),
                    "source_account_index": None,
                },
            )

    if payload.source_bank_name and not payload.source_account_id:
        # Bank name matching
        target_bank = payload.source_bank_name.lower()
        candidates = [
            a
            for a in accounts
            if target_bank in (a.get("bank_name") or "").lower()
            or (a.get("alias") and target_bank in a.get("alias").lower())
        ]
        if candidates:
            acc = candidates[0]
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch={
                    "source_account_id": str(acc.get("id")),
                    "source_bank_name": acc.get("bank_name"),
                    "source_account_name": acc.get("account_name"),
                    "source_account_number": acc.get("account_number"),
                },
            )

    if len(accounts) == 2 and payload.recipient_account:
        recipient_acc_num = payload.recipient_account

        matching_recipient = next((a for a in accounts if a.get("account_number") == recipient_acc_num), None)

        if matching_recipient:
            source_acc = next((a for a in accounts if a.get("account_number") != recipient_acc_num), None)
            if source_acc:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch={
                        "source_account_id": str(source_acc.get("id")),
                        "source_bank_name": source_acc.get("bank_name"),
                        "source_account_name": source_acc.get("account_name"),
                        "source_account_number": source_acc.get("account_number"),
                    },
                )

    accounts_list = format_accounts_list(accounts)
    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_INPUT,
        required_fields=["source_account_id"],
        prompt=accounts_list,
    )
