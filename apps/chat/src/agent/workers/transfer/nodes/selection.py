"""Source account selection logic."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.__shared__.source_account_guard import (
    build_nonready_source_account_message,
    find_account_by_bank_name,
    find_account_by_id,
    find_account_by_index,
    is_account_ready,
)
from apps.chat.src.agent.workers.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.chat.src.agent.workers.transfer.pipeline.base import TransferStep
from shared.formatters.accounts import format_accounts_list
from shared.i18n.renderer import render_message


def _build_account_options(accounts: list[dict[str, Any]]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for idx, account in enumerate(accounts, start=1):
        bank = account.get("bank_name") or "Account"
        number = str(account.get("account_number") or "")
        suffix = number[-4:] if len(number) >= 4 else number
        title = f"{bank} (···{suffix})" if suffix else str(bank)
        options.append({"id": str(idx), "title": title})
    return options


def _resolve_affinity_mode(
    payload: TransferPayload,
    *,
    explicit_resolution: bool,
) -> str:
    if explicit_resolution or payload.source_affinity_mode == "explicit":
        return "explicit"
    return "auto"


class SourceSelectionStep(TransferStep):
    """Selects source account."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any,
    ) -> TransactionResult:
        del gates, worker_context
        return await select_source_account(data, context)


async def select_source_account(
    payload: TransferPayload,
    ctx: TransferContext,
) -> TransactionResult:
    """Select source account if not provided."""
    locale = ctx.language
    linked_accounts = ctx.all_accounts or ctx.accounts
    if payload.source_account_id:
        if not payload.source_account_name:
            acc = next((a for a in ctx.accounts if str(a.get("id")) == payload.source_account_id), None)
            if acc:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch={
                        "source_account_name": acc.get("account_name"),
                        "source_affinity_mode": _resolve_affinity_mode(payload, explicit_resolution=False),
                        "funding_plan": None,
                    },
                )
            linked_account = find_account_by_id(linked_accounts, payload.source_account_id)
            if linked_account and not is_account_ready(linked_account):
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["source_account_id"],
                    prompt=render_message(
                        "source_account.choose_prompt",
                        locale,
                        {"accounts_list": format_accounts_list(ctx.accounts, locale=locale)},
                    ),
                    update_message=build_nonready_source_account_message(linked_account, locale),
                    details={"options": _build_account_options(ctx.accounts)},
                )
        return TransactionResult(outcome=TransactionOutcome.OK)

    accounts = ctx.accounts
    if not accounts:
        return TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=render_message("source_account.no_accounts", locale),
        )

    if payload.source_account_index is not None and not payload.source_account_id:
        acc = find_account_by_index(accounts, payload.source_account_index)
        if acc:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch={
                    "source_account_id": str(acc.get("id")),
                    "source_bank_name": acc.get("bank_name"),
                    "source_account_name": acc.get("account_name"),
                    "source_account_number": acc.get("account_number"),
                    "source_affinity_mode": _resolve_affinity_mode(payload, explicit_resolution=True),
                    "source_account_index": None,
                    "funding_plan": None,
                },
            )
        linked_account = find_account_by_index(linked_accounts, payload.source_account_index)
        if linked_account and not is_account_ready(linked_account):
            update_msg = build_nonready_source_account_message(linked_account, locale)
            accounts_list = format_accounts_list(accounts, locale=locale)
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["source_account_id"],
                prompt=render_message("source_account.choose_prompt", locale, {"accounts_list": accounts_list}),
                update_message=update_msg,
                patch={"source_account_index": payload.source_account_index},
                details={"options": _build_account_options(accounts)},
            )

    if payload.source_bank_name and not payload.source_account_id:
        acc = find_account_by_bank_name(accounts, payload.source_bank_name)
        if acc:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch={
                    "source_account_id": str(acc.get("id")),
                    "source_bank_name": acc.get("bank_name"),
                    "source_account_name": acc.get("account_name"),
                    "source_account_number": acc.get("account_number"),
                    "source_affinity_mode": _resolve_affinity_mode(payload, explicit_resolution=True),
                    "funding_plan": None,
                },
            )
        linked_account = find_account_by_bank_name(linked_accounts, payload.source_bank_name)
        update_msg = (
            build_nonready_source_account_message(linked_account, locale)
            if linked_account and not is_account_ready(linked_account)
            else render_message(
                "source_account.bank_not_found",
                locale,
                {"bank_name": payload.source_bank_name or ""},
            )
        )
        accounts_list = format_accounts_list(accounts, locale=locale)
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["source_account_id"],
            prompt=render_message("source_account.choose_prompt", locale, {"accounts_list": accounts_list}),
            update_message=update_msg,
            patch={"source_bank_name": payload.source_bank_name},
            details={"options": _build_account_options(accounts)},
        )

    if len(accounts) == 1:
        acc = accounts[0]
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "source_account_id": str(acc.get("id")),
                "source_bank_name": acc.get("bank_name"),
                "source_account_name": acc.get("account_name"),
                "source_account_number": acc.get("account_number"),
                "source_affinity_mode": _resolve_affinity_mode(payload, explicit_resolution=False),
                "funding_plan": None,
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
                "source_affinity_mode": _resolve_affinity_mode(payload, explicit_resolution=False),
                "funding_plan": None,
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
                        "source_affinity_mode": _resolve_affinity_mode(payload, explicit_resolution=False),
                        "funding_plan": None,
                    },
                )

    accounts_list = format_accounts_list(accounts, locale=locale)

    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_INPUT,
        required_fields=["source_account_id"],
        prompt=render_message("source_account.choose_prompt", locale, {"accounts_list": accounts_list}),
        details={"options": _build_account_options(accounts)},
    )
