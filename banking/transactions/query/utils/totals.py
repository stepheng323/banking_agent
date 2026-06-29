"""Shared utilities for financial totals and analytics."""

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from banking.transactions.query.models.domain import AccountCashFlowBreakdown


class FinancialTotals(BaseModel):
    """Canonical representation of debit/credit totals over a set of transactions."""

    total_inflow: int = 0
    total_outflow: int = 0
    inflow_count: int = 0
    outflow_count: int = 0
    excluded_internal_count: int = 0
    excluded_reversals_count: int = 0
    account_breakdowns: dict[str, AccountCashFlowBreakdown]
    settled_transactions: list[dict[str, Any]]

    @property
    def net_flow(self) -> int:
        return self.total_inflow - self.total_outflow


def calculate_financial_totals(
    transactions: Iterable[dict[str, Any]],
    account_id_scope: str | None = None,
) -> FinancialTotals:
    """
    Calculate centralized financial totals.

    Rules for inclusion:
    - Only posted/success/completed transactions are included in totals.
    - Internal transfers (own-account) are excluded from totals.
    - Reversals and failed transactions are excluded from totals.
    """
    total_inflow = 0
    total_outflow = 0
    inflow_count = 0
    outflow_count = 0

    excluded_internal_count = 0
    excluded_reversals_count = 0
    settled_transactions: list[dict[str, Any]] = []

    account_breakdown_map: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "bank_name": "Unknown account",
            "masked_account_number": "",
            "total_inflow": 0,
            "total_outflow": 0,
        }
    )

    for t in transactions:
        raw_status = (
            t.get("display_status")
            or t.get("status")
            or t.get("local_status")
            or t.get("provider_status")
            or ""
        )
        status = str(raw_status).lower()
        is_settled = False
        if status and status not in {"posted", "success", "successful", "completed", "complete"}:
            if "revers" in status or "fail" in status:
                excluded_reversals_count += 1
        else:
            is_settled = True

        if not is_settled:
            continue

        is_internal = t.get("is_internal_transfer", False)
        if not is_internal:
            narration = str(t.get("narration") or "").lower()
            if "internal transfer" in narration or "own account" in narration:
                is_internal = True

        if is_internal:
            excluded_internal_count += 1
            continue

        settled_transactions.append(t)

        acc_id = str(t.get("source_account_id") or account_id_scope or "unknown")

        bank_name = t.get("bank_name") or t.get("source_account_label") or account_breakdown_map[acc_id]["bank_name"]
        account_breakdown_map[acc_id]["bank_name"] = bank_name

        acc_num = t.get("source_account_number") or account_breakdown_map[acc_id]["masked_account_number"]
        account_breakdown_map[acc_id]["masked_account_number"] = acc_num

        amount = abs(float(t.get("amount", 0)))
        tx_type = t.get("type", "unknown")

        if tx_type == "credit":
            total_inflow += int(amount)
            inflow_count += 1
            account_breakdown_map[acc_id]["total_inflow"] += int(amount)
        elif tx_type == "debit":
            total_outflow += int(amount)
            outflow_count += 1
            account_breakdown_map[acc_id]["total_outflow"] += int(amount)

    breakdowns = {}
    for acc_id, data in account_breakdown_map.items():
        total_in = int(data.get("total_inflow", 0))
        total_out = int(data.get("total_outflow", 0))
        if total_in > 0 or total_out > 0:
            mask = str(data["masked_account_number"])[-4:] if data["masked_account_number"] else ""
            breakdowns[acc_id] = AccountCashFlowBreakdown(
                account_id=acc_id,
                bank_name=str(data["bank_name"]),
                masked_account_number=mask,
                total_inflow=total_in,
                total_outflow=total_out,
                net_flow=total_in - total_out,
            )

    return FinancialTotals(
        total_inflow=total_inflow,
        total_outflow=total_outflow,
        inflow_count=inflow_count,
        outflow_count=outflow_count,
        excluded_internal_count=excluded_internal_count,
        excluded_reversals_count=excluded_reversals_count,
        account_breakdowns=breakdowns,
        settled_transactions=settled_transactions,
    )
