"""Source account selection logic for Airtime."""

from typing import Any

from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.formatters.accounts import format_accounts_list
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class SourceSelectionStep(AirtimeStep):
    """Selects source account."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        if data.source_account_id:
            logger.info("airtime_selection_preselected", source_account_id=data.source_account_id)
            return TransactionResult(outcome=TransactionOutcome.OK)

        accounts = context.accounts
        logger.info("airtime_selection_check", account_count=len(accounts))

        if not accounts:
            logger.warning("airtime_selection_no_accounts")
            return TransactionResult(outcome=TransactionOutcome.FAILED, error="No accounts available.")

        if len(accounts) == 1:
            acc = accounts[0]
            logger.info("airtime_selection_single_account", account_id=acc.get("id"))
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
            logger.info("airtime_selection_default_found", default_id=default.get("id"))
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch={
                    "source_account_id": str(default.get("id")),
                    "source_bank_name": default.get("bank_name"),
                    "source_account_name": default.get("account_name"),
                    "source_account_number": default.get("account_number"),
                },
            )

        if data.source_bank_name and not data.source_account_id:
            # Bank name matching
            target_bank = data.source_bank_name.lower()
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
            else:
                # Feedback: requested bank not found
                update_msg = f"I couldn't find your {data.source_bank_name} account."
                accounts_list = format_accounts_list(accounts)
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["source_account_id"],
                    prompt=f"*Which account would you like to use?*\n\n{accounts_list}",
                    update_message=update_msg,
                    patch={"source_bank_name": data.source_bank_name},
                )

        if data.source_account_index is not None:
            index = data.source_account_index - 1
            if 0 <= index < len(accounts):
                acc = accounts[index]
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch={
                        "source_account_id": str(acc.get("id")),
                        "source_bank_name": acc.get("bank_name"),
                        "source_account_name": acc.get("account_name"),
                        "source_account_number": acc.get("account_number"),
                        "source_account_index": None,  # clear index
                    },
                )

        accounts_list = format_accounts_list(accounts)
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["source_account_id"],
            prompt=f"*Which account would you like to use?*\n\n{accounts_list}",
        )
