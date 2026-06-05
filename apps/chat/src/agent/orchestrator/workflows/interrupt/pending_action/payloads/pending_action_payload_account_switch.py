"""Account-switch payload overrides for pending transaction confirmations."""

from types import SimpleNamespace
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState

from ..targets.pending_action_targets import TRANSACTION_INTENTS
from .pending_action_payload_overrides import _pending_edit_payload_overrides_from_fields
from .pending_action_source_account_resolution import _resolve_source_bank_name_from_account_reference


def _account_switch_source_overrides(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
    text: str,
) -> dict[str, dict[str, Any]]:
    source_bank_name = _resolve_source_bank_name_from_account_reference(
        state=state,
        decision=decision,
        text=text,
    )
    if not source_bank_name:
        return {}

    target = SimpleNamespace(
        target_task_ids=[],
        target_texts=[],
        target_types=list(TRANSACTION_INTENTS),
    )
    return _pending_edit_payload_overrides_from_fields(
        state=state,
        interrupt=interrupt,
        target=target,
        fields={"source_bank_name": source_bank_name},
    )


__all__ = ["_account_switch_source_overrides"]
