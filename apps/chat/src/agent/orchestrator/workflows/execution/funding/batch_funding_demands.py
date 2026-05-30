from typing import Any, cast

from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES
from banking.transfers.funding.batch_models import SourceAffinity, TransferDemand
from shared.money import MoneyAmount, require_naira, to_naira


def _build_transfer_demand(task_id: str, task_payload: dict[str, Any]) -> TransferDemand:
    mode = "explicit" if task_payload.get("source_affinity_mode") == "explicit" else "auto"
    source_accounts_raw = task_payload.get("source_accounts")
    source_accounts = source_accounts_raw if isinstance(source_accounts_raw, list) else []
    explicit_sources = [str(bank) for bank in source_accounts if str(bank).strip()]
    source_bank_name = task_payload.get("source_bank_name")
    if mode == "explicit" and not explicit_sources and isinstance(source_bank_name, str) and source_bank_name.strip():
        explicit_sources = [source_bank_name.strip()]

    explicit_split_raw = task_payload.get("explicit_split")
    explicit_split: dict[str, MoneyAmount] | None = None
    if isinstance(explicit_split_raw, dict):
        explicit_split = {
            str(key): amount
            for key, value in explicit_split_raw.items()
            if str(key).strip() and (amount := to_naira(value)) is not None and amount > 0
        }
    preferred_account_id = task_payload.get("source_account_id")
    return TransferDemand(
        task_id=task_id,
        amount=to_naira(task_payload.get("amount")) or require_naira(0),
        source_affinity=SourceAffinity(mode=cast(Any, mode)),
        explicit_sources=explicit_sources,
        explicit_split=explicit_split,
        use_dual_accounts=bool(task_payload.get("use_dual_accounts")),
        preferred_account_id=str(preferred_account_id) if preferred_account_id else None,
    )


def _is_plannable_transfer_task(task: Any) -> bool:
    if not task or task.type != "transfer" or task.stage in TERMINAL_STAGES:
        return False
    payload = task.payload if isinstance(task.payload, dict) else {}
    amount = payload.get("amount")
    parsed_amount = to_naira(amount)
    return (
        bool(payload.get("source_account_id"))
        and not payload.get("funding_plan")
        and parsed_amount is not None
        and parsed_amount > 0
    )


__all__ = ["_build_transfer_demand", "_is_plannable_transfer_task"]
