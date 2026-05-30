from typing import Any

from shared.money import money_to_json, to_money


def _build_planning_signature(task_payload: dict[str, Any]) -> dict[str, Any]:
    explicit_split_raw = task_payload.get("explicit_split")
    explicit_split = explicit_split_raw if isinstance(explicit_split_raw, dict) else {}
    normalized_split = {
        str(k): money_to_json(v)
        for k, v in sorted(explicit_split.items(), key=lambda item: str(item[0]))
        if to_money(v) is not None
    }
    source_accounts_raw = task_payload.get("source_accounts")
    source_accounts = source_accounts_raw if isinstance(source_accounts_raw, list) else []
    return {
        "planned_for_amount": money_to_json(task_payload.get("amount")) or "0.00",
        "planned_for_source_account_id": task_payload.get("source_account_id"),
        "planned_for_source_accounts": sorted([str(bank) for bank in source_accounts if str(bank).strip()]),
        "planned_for_use_dual_accounts": bool(task_payload.get("use_dual_accounts")),
        "planned_for_explicit_split": normalized_split,
    }


def _funding_plan_to_payload_dict(plan: Any, task_payload: dict[str, Any]) -> dict[str, Any]:
    signature = _build_planning_signature(task_payload)
    return {
        "transfer_amount": money_to_json(plan.transfer_amount),
        "total_funded": money_to_json(plan.total_funded),
        "is_sufficient": bool(plan.is_sufficient),
        "is_single_source": len(plan.steps) == 1,
        "trigger_mode": plan.trigger_mode,
        "requested_sources": list(plan.requested_sources),
        "explicit_split_applied": bool(plan.explicit_split_applied),
        "primary_account_id": str(plan.primary_account_id) if plan.primary_account_id else None,
        "primary_bank_name": plan.primary_bank_name,
        "primary_available_balance": money_to_json(plan.primary_available_balance),
        "planned_for_amount": signature["planned_for_amount"],
        "planned_for_source_account_id": signature["planned_for_source_account_id"],
        "planned_for_source_accounts": signature["planned_for_source_accounts"],
        "planned_for_use_dual_accounts": signature["planned_for_use_dual_accounts"],
        "planned_for_explicit_split": signature["planned_for_explicit_split"],
        "steps": [
            {
                "account_id": str(step.account_id),
                "amount": money_to_json(step.amount),
                "bank_name": step.bank_name,
                "sequence": int(step.sequence),
            }
            for step in plan.steps
        ],
    }


__all__ = ["_build_planning_signature", "_funding_plan_to_payload_dict"]
