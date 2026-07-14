"""Per-field payload patch routing for pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view

from ..patches.pending_action_amount_patches import (
    _typed_amount_patch,
)
from ..patches.pending_action_data_plan_patches import (
    _data_plan_preference_patch,
)
from ..patches.pending_action_funding_patches import (
    _funding_splits_patch,
    _source_accounts_patch,
    _use_dual_accounts_patch,
)
from ..patches.pending_action_mobile_patches import (
    _network_patch,
    _phone_patch,
)
from ..patches.pending_action_source_account_patches import (
    _source_account_patch,
)
from ..patches.pending_action_transfer_patches import (
    _narration_patch,
    _recipient_patch,
)
from ..targets.pending_action_targets import (
    _DATA_PLAN_EDIT_FIELDS,
    TRANSACTION_INTENTS,
)


def _pending_edit_patch_for_field(
    *,
    state: OrchestratorState,
    task: Any,
    field: Any,
    value: Any,
    amount_mutation: Any = None,
) -> dict[str, Any] | None:
    if field == "narration" and task.type == "transfer":
        return _narration_patch(value)
    if field == "amount" and task.type in TRANSACTION_INTENTS:
        return _typed_amount_patch(
            amount_mutation,
            task_type=task.type,
            current_amount=task.payload.get("amount"),
            legacy_amount=value,
        )
    if field in {"recipient_name", "recipient_account", "recipient_bank_name"} and task.type == "transfer":
        return _recipient_patch(str(field), value)
    if field == "source_bank_name" and task.type in TRANSACTION_INTENTS:
        return _source_account_patch(state=state, source_bank_name=value)
    if field == "source_account_index" and task.type in TRANSACTION_INTENTS:
        return _source_account_patch(state=state, source_account_index=value)
    if field == "source_accounts" and task.type == "transfer":
        return _source_accounts_patch(value)
    if field == "use_dual_accounts" and task.type == "transfer":
        return _use_dual_accounts_patch(value)
    if field == "funding_splits" and task.type == "transfer":
        return _funding_splits_patch(value)
    if field == "phone" and task.type in {"airtime", "data"}:
        return _phone_patch(task.type, value, payload=task.payload, user_phone=interrupt_state_view(state).phone_number)
    if field == "network" and task.type in {"airtime", "data"}:
        return _network_patch(task.type, value, payload=task.payload)
    if field in _DATA_PLAN_EDIT_FIELDS and task.type == "data":
        return _data_plan_preference_patch(str(field), value, task.payload)
    return None


__all__ = ["_pending_edit_patch_for_field"]
