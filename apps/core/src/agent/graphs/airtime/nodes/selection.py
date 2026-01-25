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
            prompt=accounts_list,
        )
