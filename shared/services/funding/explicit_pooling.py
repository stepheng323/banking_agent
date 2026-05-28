"""Explicit source-account funding strategies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import shared.services.funding.account_matching as account_matching
import shared.services.funding.models as funding_models
from shared.formatters.currency import format_naira
from shared.formatters.funding import format_insufficient_funds
from shared.i18n.renderer import render_message
from shared.policy.transaction_limits import MAX_POOLED_SOURCE_ACCOUNTS

BalanceFetcher = Callable[[Any], Awaitable[float]]


async def plan_explicit_pooling(
    *,
    eligible: list[Any],
    all_accounts: list[Any],
    transfer_amount: float,
    requested_source_banks: list[str],
    explicit_split: dict[str, float],
    force_multi_source: bool,
    locale: str,
    fetch_balance: BalanceFetcher,
) -> funding_models.FundingPlan:
    requested_accounts: list[Any] = []
    if requested_source_banks:
        for bank_name in requested_source_banks:
            account = account_matching.match_account_by_bank_name(eligible, bank_name)
            if account and all(str(existing.id) != str(account.id) for existing in requested_accounts):
                requested_accounts.append(account)
                continue

            ineligible = account_matching.match_ineligible_requested_account(all_accounts, eligible, bank_name)
            if ineligible is not None:
                return funding_models.FundingPlan(
                    transfer_amount=transfer_amount,
                    total_funded=0,
                    is_sufficient=False,
                    trigger_mode="explicit",
                    requested_sources=requested_source_banks,
                    error=account_matching.build_explicit_nonready_account_message(ineligible, locale),
                )

        if not requested_accounts:
            return funding_models.FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=0,
                is_sufficient=False,
                trigger_mode="explicit",
                requested_sources=requested_source_banks,
                error=render_message(
                    "funding.planner.preferred_account_not_eligible",
                    locale,
                ),
            )

    candidates = requested_accounts or eligible

    if explicit_split:
        return await plan_with_explicit_split(
            candidates=candidates,
            all_accounts=all_accounts,
            transfer_amount=transfer_amount,
            explicit_split=explicit_split,
            locale=locale,
            requested_source_banks=requested_source_banks,
            fetch_balance=fetch_balance,
        )

    candidates = candidates[:MAX_POOLED_SOURCE_ACCOUNTS]
    steps: list[funding_models.FundingStepPlan] = []
    remaining = transfer_amount
    balance_checks = 0
    sequence = 1
    balances: list[tuple[Any, float]] = []
    for account in candidates:
        available = await fetch_balance(account)
        balance_checks += 1
        balances.append((account, available))

    if force_multi_source and len(balances) >= 2 and transfer_amount > 0:
        first_account, first_balance = balances[0]
        second_account, second_balance = balances[1]
        first_target = transfer_amount / 2
        first_contribution = min(first_balance, first_target)
        second_contribution = min(second_balance, transfer_amount - first_contribution)
        remaining_after_two = transfer_amount - (first_contribution + second_contribution)

        if remaining_after_two > 0 and first_balance > first_contribution:
            extra = min(first_balance - first_contribution, remaining_after_two)
            first_contribution += extra
            remaining_after_two -= extra

        if first_contribution > 0:
            steps.append(account_matching.create_step(first_account, first_contribution, sequence))
            sequence += 1
        if second_contribution > 0:
            steps.append(account_matching.create_step(second_account, second_contribution, sequence))
            sequence += 1
        remaining = remaining_after_two

    for account, balance in balances:
        if remaining <= 0 or len(steps) >= MAX_POOLED_SOURCE_ACCOUNTS:
            break
        if any(str(existing.account_id) == str(account.id) for existing in steps):
            continue
        contribution = min(balance, remaining)
        if contribution > 0:
            steps.append(account_matching.create_step(account, contribution, sequence))
            remaining -= contribution
            sequence += 1

    total_funded = transfer_amount - max(0, remaining)
    is_sufficient = remaining <= 0
    plan = funding_models.FundingPlan(
        transfer_amount=transfer_amount,
        total_funded=total_funded,
        steps=steps,
        is_sufficient=is_sufficient,
        shortfall=max(0, remaining),
        balance_checks=balance_checks,
        trigger_mode="explicit",
        requested_sources=requested_source_banks,
        explicit_split_applied=False,
        primary_account_id=steps[0].account_id if steps else None,
        primary_bank_name=steps[0].bank_name if steps else None,
        primary_available_balance=balances[0][1] if balances else None,
    )
    if not is_sufficient:
        plan.error = format_insufficient_funds(
            transfer_amount=transfer_amount,
            bank_name=steps[0].bank_name if steps else render_message("funding.format.plan.bank_fallback", locale),
            available_balance=steps[0].amount if steps else 0.0,
            max_available=total_funded,
            locale=locale,
        )
    return plan


async def plan_with_explicit_split(
    *,
    candidates: list[Any],
    all_accounts: list[Any],
    transfer_amount: float,
    explicit_split: dict[str, float],
    locale: str,
    requested_source_banks: list[str],
    fetch_balance: BalanceFetcher,
) -> funding_models.FundingPlan:
    if len(explicit_split) > MAX_POOLED_SOURCE_ACCOUNTS:
        return funding_models.FundingPlan(
            transfer_amount=transfer_amount,
            total_funded=0,
            is_sufficient=False,
            trigger_mode="explicit",
            requested_sources=requested_source_banks,
            explicit_split_applied=True,
            error=("Please use at most 2 source accounts in your split. Revise the split and try again."),
        )

    split_total = round(sum(explicit_split.values()), 2)
    if abs(split_total - transfer_amount) > 0.01:
        return funding_models.FundingPlan(
            transfer_amount=transfer_amount,
            total_funded=0,
            is_sufficient=False,
            trigger_mode="explicit",
            requested_sources=requested_source_banks,
            explicit_split_applied=True,
            error=(
                "Your split does not match the transfer amount. "
                f"Requested total: {format_naira(split_total)}, transfer amount: {format_naira(transfer_amount)}. "
                "Please revise the split."
            ),
        )

    steps: list[funding_models.FundingStepPlan] = []
    balance_checks = 0
    sequence = 1
    total_funded = 0.0
    used_account_ids: set[str] = set()
    primary_available_balance: float | None = None
    for requested_bank, requested_amount in explicit_split.items():
        account = account_matching.match_account_by_bank_name(candidates, requested_bank)
        if account is None:
            ineligible = account_matching.match_ineligible_requested_account(all_accounts, candidates, requested_bank)
            if ineligible is not None:
                return funding_models.FundingPlan(
                    transfer_amount=transfer_amount,
                    total_funded=total_funded,
                    is_sufficient=False,
                    trigger_mode="explicit",
                    requested_sources=requested_source_banks,
                    explicit_split_applied=True,
                    error=account_matching.build_explicit_nonready_account_message(ineligible, locale),
                )
            return funding_models.FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=total_funded,
                is_sufficient=False,
                trigger_mode="explicit",
                requested_sources=requested_source_banks,
                explicit_split_applied=True,
                error=(
                    f"I could not match '{requested_bank}' to your eligible linked accounts. "
                    "Please revise the split."
                ),
            )

        account_id = str(account.id)
        if account_id in used_account_ids:
            return funding_models.FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=total_funded,
                is_sufficient=False,
                trigger_mode="explicit",
                requested_sources=requested_source_banks,
                explicit_split_applied=True,
                error="Please use distinct source accounts in your split and try again.",
            )
        used_account_ids.add(account_id)

        available = await fetch_balance(account)
        balance_checks += 1
        if primary_available_balance is None:
            primary_available_balance = available
        if available < requested_amount:
            return funding_models.FundingPlan(
                transfer_amount=transfer_amount,
                total_funded=total_funded,
                is_sufficient=False,
                trigger_mode="explicit",
                requested_sources=requested_source_banks,
                explicit_split_applied=True,
                error=(
                    f"Your split is not feasible: {account.bank_name} has {format_naira(available)}, "
                    f"but you requested {format_naira(requested_amount)}. Please revise the split."
                ),
            )

        steps.append(account_matching.create_step(account, requested_amount, sequence))
        total_funded += requested_amount
        sequence += 1

    return funding_models.FundingPlan(
        transfer_amount=transfer_amount,
        total_funded=total_funded,
        steps=steps,
        is_sufficient=True,
        shortfall=0.0,
        balance_checks=balance_checks,
        trigger_mode="explicit",
        requested_sources=requested_source_banks,
        explicit_split_applied=True,
        primary_account_id=steps[0].account_id if steps else None,
        primary_bank_name=steps[0].bank_name if steps else None,
        primary_available_balance=primary_available_balance,
    )
