from typing import Any

from apps.core.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.core.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.formatters.accounts import format_accounts_list
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class SourceSelectionStep(PipelineStep):
    """Selection Step: Select source account."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        if payload.source_account_id:
            if not payload.source_account_name:
                acc = next((a for a in context.accounts if str(a.get("id")) == payload.source_account_id), None)
                if acc:
                    payload.source_account_name = acc.get("account_name")
                    payload.source_bank_name = acc.get("bank_name")
                    payload.source_account_number = acc.get("account_number")
            return None

        accounts = context.accounts
        if not accounts:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error="No accounts available.",
            )

        if len(accounts) == 1:
            acc = accounts[0]
            payload.source_account_id = str(acc.get("id"))
            payload.source_bank_name = acc.get("bank_name")
            payload.source_account_name = acc.get("account_name")
            payload.source_account_number = acc.get("account_number")
            return None

        default = next((a for a in accounts if a.get("is_default")), None)
        if default:
            payload.source_account_id = str(default.get("id"))
            payload.source_bank_name = default.get("bank_name")
            payload.source_account_name = default.get("account_name")
            payload.source_account_number = default.get("account_number")
            return None

        if payload.source_account_index is not None:
            index = payload.source_account_index - 1
            if 0 <= index < len(accounts):
                acc = accounts[index]
                payload.source_account_id = str(acc.get("id"))
                payload.source_bank_name = acc.get("bank_name")
                payload.source_account_name = acc.get("account_name")
                payload.source_account_number = acc.get("account_number")
                payload.source_account_index = None
                return None

        if payload.source_bank_name:
            target_bank = payload.source_bank_name.lower()
            candidates = [
                a
                for a in accounts
                if target_bank in (a.get("bank_name") or "").lower()
                or (a.get("alias") and target_bank in a.get("alias").lower())
            ]
            if candidates:
                acc = candidates[0]
                payload.source_account_id = str(acc.get("id"))
                payload.source_bank_name = acc.get("bank_name")
                payload.source_account_name = acc.get("account_name")
                payload.source_account_number = acc.get("account_number")
                return None
            else:
                # Feedback: requested bank not found
                update_msg = f"I couldn't find your {payload.source_bank_name} account."
                accounts_list = format_accounts_list(accounts)
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["source_account_id"],
                    prompt=f"*Which account would you like to use?*\n\n{accounts_list}",
                    update_message=update_msg,
                    patch={"source_bank_name": payload.source_bank_name},
                )

        accounts_list = format_accounts_list(accounts)
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["source_account_id"],
            prompt=f"*Which account would you like to use?*\n\n{accounts_list}",
        )
