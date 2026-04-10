"""Source account selection logic for Airtime."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.source_account_guard import (
    build_nonready_source_account_message,
    find_account_by_bank_name,
    find_account_by_id,
    find_account_by_index,
    is_account_ready,
)
from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.formatters.accounts import format_accounts_list
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _build_account_options(accounts: list[dict[str, Any]]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for idx, account in enumerate(accounts, start=1):
        bank = account.get("bank_name") or "Account"
        number = str(account.get("account_number") or "")
        suffix = number[-4:] if len(number) >= 4 else number
        title = f"{bank} (···{suffix})" if suffix else str(bank)
        options.append({"id": str(idx), "title": title})
    return options


class SourceSelectionStep(AirtimeStep):
    """Selects source account."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        del gates, worker_context
        locale = context.language
        linked_accounts = context.all_accounts or context.accounts
        if data.source_account_id:
            linked_account = find_account_by_id(linked_accounts, data.source_account_id)
            if linked_account and not is_account_ready(linked_account):
                accounts_list = format_accounts_list(context.accounts, locale=locale)
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["source_account_id"],
                    prompt=render_message("source_account.choose_prompt", locale, {"accounts_list": accounts_list}),
                    update_message=build_nonready_source_account_message(linked_account, locale),
                    details={"options": _build_account_options(context.accounts)},
                )
            logger.info("airtime_selection_preselected", source_account_id=data.source_account_id)
            return TransactionResult(outcome=TransactionOutcome.OK)

        accounts = context.accounts
        logger.info("airtime_selection_check", account_count=len(accounts))

        if not accounts:
            logger.warning("airtime_selection_no_accounts")
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("source_account.no_accounts", locale),
            )

        if data.source_bank_name and not data.source_account_id:
            acc = find_account_by_bank_name(accounts, data.source_bank_name)
            if acc:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch={
                        "source_account_id": str(acc.get("id")),
                        "source_bank_name": acc.get("bank_name"),
                        "source_account_name": acc.get("account_name"),
                        "source_account_number": acc.get("account_number"),
                    },
                )
            linked_account = find_account_by_bank_name(linked_accounts, data.source_bank_name)
            update_msg = (
                build_nonready_source_account_message(linked_account, locale)
                if linked_account and not is_account_ready(linked_account)
                else render_message(
                    "source_account.bank_not_found",
                    locale,
                    {"bank_name": data.source_bank_name or ""},
                )
            )
            accounts_list = format_accounts_list(accounts, locale=locale)
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["source_account_id"],
                prompt=render_message("source_account.choose_prompt", locale, {"accounts_list": accounts_list}),
                update_message=update_msg,
                patch={"source_bank_name": data.source_bank_name},
                details={"options": _build_account_options(accounts)},
            )

        if data.source_account_index is not None:
            acc = find_account_by_index(accounts, data.source_account_index)
            if acc:
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
            linked_account = find_account_by_index(linked_accounts, data.source_account_index)
            if linked_account and not is_account_ready(linked_account):
                accounts_list = format_accounts_list(accounts, locale=locale)
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["source_account_id"],
                    prompt=render_message("source_account.choose_prompt", locale, {"accounts_list": accounts_list}),
                    update_message=build_nonready_source_account_message(linked_account, locale),
                    patch={"source_account_index": data.source_account_index},
                    details={"options": _build_account_options(accounts)},
                )

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

        accounts_list = format_accounts_list(accounts, locale=locale)
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["source_account_id"],
            prompt=render_message("source_account.choose_prompt", locale, {"accounts_list": accounts_list}),
            details={"options": _build_account_options(accounts)},
        )
