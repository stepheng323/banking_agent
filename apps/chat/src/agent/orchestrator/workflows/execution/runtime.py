"""Typed runtime state and helpers for execution task handlers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha1
from typing import Any, Literal, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices, WorkerName
from banking.presentation.i18n.locale import LocaleManager
from banking.runtime.protocols import WorkerProtocol
from banking.runtime.results import TransactionOutcome
from shared.utils.logging import get_logger
from shared.utils.serialization import sqlalchemy_to_dict

logger = get_logger(__name__)

ExecutionResultPatch = dict[str, Any]
_RECIPIENT_PRONOUN_TOKENS = {"her", "him", "them", "that", "it", "this", "previous"}
_TERMINAL_TRANSACTION_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}


class ExecutionAccumulator:
    """Mutable reducer surface for one execution wave."""

    def __init__(self, tasks: dict[str, TaskSpec]) -> None:
        self.updates: dict[str, Any] = {"tasks": tasks}
        self.missing_fields_by_task: dict[str, list[str]] = {}
        self.details_by_task: dict[str, dict[str, Any]] = {}
        self.needs_confirm_tasks: list[str] = []
        self.needs_auth_tasks: list[str] = []
        self.prompts: list[str] = []
        self.prompts_by_task: dict[str, str] = {}
        self.feedback_messages: list[str] = []
        self.source_bank_hints: list[str] = []

    def set_update(self, key: str, value: Any) -> None:
        self.updates[key] = value

    def get_update(self, key: str, default: Any = None) -> Any:
        return self.updates.get(key, default)

    def add_outbox(self, entry: dict[str, Any]) -> None:
        self.updates.setdefault("outbox", [])
        self.updates["outbox"].append(entry)

    def extend_outbox(self, entries: list[dict[str, Any]] | None) -> None:
        if entries:
            self.updates.setdefault("outbox", [])
            self.updates["outbox"].extend(entries)

    def say(self, text: str | None) -> None:
        if text:
            self.add_outbox({"type": "say", "text": text})

    def add_prompt(self, prompt: str | None, task_id: str | None = None) -> None:
        if prompt:
            self.prompts.append(prompt)
            if task_id:
                self.prompts_by_task[task_id] = prompt

    def add_missing_fields(self, task_id: str, fields: list[str] | None) -> None:
        if fields:
            self.missing_fields_by_task[task_id] = fields

    def add_details(self, task_id: str, details: dict[str, Any] | None) -> None:
        if details:
            self.details_by_task[task_id] = details


@dataclass
class ExecutionTurnContext:
    state: OrchestratorState
    config: RunnableConfig
    services: OrchestrationServices
    current_wave_len: int
    accumulator: ExecutionAccumulator
    current_wave_task_ids: list[str] | None = None

    @property
    def configurable(self) -> Mapping[str, Any]:
        configurable = self.config.get("configurable", {})
        if isinstance(configurable, Mapping):
            return cast(Mapping[str, Any], configurable)
        return {}

    def config_value(self, key: str, default: Any = None) -> Any:
        return self.configurable.get(key, default)

    def require_worker(
        self,
        name: WorkerName,
        task: TaskSpec,
        *,
        log_key: str,
        error_message: str,
    ) -> WorkerProtocol | None:
        return self.services.require(name, task, log_key=log_key, error_message=error_message)


def _state_locale(state: OrchestratorState) -> str:
    return cast(str, LocaleManager.normalize(state.loaded_context.get("language")).value)


def _stamp_async_group_metadata(task: TaskSpec, ctx: ExecutionTurnContext) -> None:
    if task.type not in {"transfer", "airtime", "data"}:
        return

    transaction_types = {"transfer", "airtime", "data"}
    current_wave_task_ids = ctx.current_wave_task_ids or []
    current_wave_transaction_task_ids = [
        task_id
        for task_id in current_wave_task_ids
        if (wave_task := ctx.state.tasks.get(task_id)) is not None and wave_task.type in transaction_types
    ]

    previous_group_id = task.payload.get("async_group_id")
    candidate_task_ids = set(current_wave_transaction_task_ids)
    if previous_group_id:
        candidate_task_ids.update(
            task_id
            for task_id, sibling in ctx.state.tasks.items()
            if sibling.type in transaction_types
            and sibling.stage not in _TERMINAL_TRANSACTION_STAGES
            and sibling.payload.get("async_group_id") == previous_group_id
        )

    transaction_task_ids = [task_id for task_id in ctx.state.tasks if task_id in candidate_task_ids]
    for task_id in current_wave_transaction_task_ids:
        if task_id not in transaction_task_ids:
            transaction_task_ids.append(task_id)

    if not transaction_task_ids:
        return

    group_size = len(transaction_task_ids)
    group_kind: Literal["single", "multi_transfer", "mixed_batch"]
    if group_size == 1:
        group_kind = "single"
    elif all(ctx.state.tasks[task_id].type == "transfer" for task_id in transaction_task_ids):
        group_kind = "multi_transfer"
    else:
        group_kind = "mixed_batch"

    group_fingerprint = "|".join(
        [
            str(ctx.state.last_message_id or ""),
            str(ctx.state.current_wave_index),
            *transaction_task_ids,
        ]
    )
    group_id = sha1(group_fingerprint.encode("utf-8")).hexdigest()[:20]

    for index, task_id in enumerate(transaction_task_ids, start=1):
        grouped_task = ctx.state.tasks.get(task_id)
        if grouped_task is None or grouped_task.type not in transaction_types:
            continue
        grouped_task.payload["async_group_id"] = group_id
        grouped_task.payload["async_group_size"] = group_size
        grouped_task.payload["async_group_kind"] = group_kind
        grouped_task.payload["async_group_index"] = index


def _normalize_beneficiary_rows(rows: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(row)
        else:
            normalized.append(sqlalchemy_to_dict(row))
    return normalized


def _recipient_supports_targeted_beneficiary_lookup(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    if not normalized:
        return False
    return normalized not in _RECIPIENT_PRONOUN_TOKENS


def _normalize_beneficiary_match_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def _beneficiary_cache_contains_recipient(beneficiaries: list[dict[str, Any]], recipient_name: str) -> bool:
    requested = _normalize_beneficiary_match_text(recipient_name)
    if not requested:
        return False

    for beneficiary in beneficiaries:
        alias = _normalize_beneficiary_match_text(beneficiary.get("alias"))
        account_name = _normalize_beneficiary_match_text(beneficiary.get("account_name"))
        if alias and (requested in alias or alias in requested):
            return True
        if account_name and (requested in account_name or account_name in requested):
            return True
    return False


def _maybe_user_message(task: TaskSpec, state: OrchestratorState) -> str | None:
    logger.info(
        "maybe_user_msg_check",
        task_id=task.id,
        last_int=state.last_interrupt.task_ids if state.last_interrupt else None,
    )
    if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
        # Only tasks that asked for input consume the current user text.
        if state.last_interrupt and task.id not in state.last_interrupt.task_ids:
            return None
        scoped_user_message = task.payload.pop("pending_user_message", None)
        if isinstance(scoped_user_message, str) and scoped_user_message.strip():
            return scoped_user_message
        return cast(str | None, state.last_message_text)
    return None


def _get_worker(
    services: OrchestrationServices,
    name: WorkerName,
    task: TaskSpec,
    *,
    log_key: str,
    error_message: str,
) -> WorkerProtocol | None:
    return services.require(name, task, log_key=log_key, error_message=error_message)


def _apply_result_patch(task: TaskSpec, result: Any) -> None:
    patch = getattr(result, "patch", None)
    if patch:
        task.payload.update(cast(ExecutionResultPatch, patch))


def _next_query_handoff_transfer_task_id(tasks: dict[str, Any]) -> str:
    index = 1
    candidate = f"query_handoff_transfer_{index}"
    while candidate in tasks:
        index += 1
        candidate = f"query_handoff_transfer_{index}"
    return candidate


def _set_confirmation(task: TaskSpec, result: Any, *, gate_on: str) -> None:
    if gate_on == "summary" and not getattr(result, "confirmation_summary", None):
        return
    if gate_on == "snapshot" and not getattr(result, "confirmation_snapshot", None):
        return

    confirmation = task.payload.setdefault("confirmation", {})
    confirmation["summary"] = getattr(result, "confirmation_summary", None)
    confirmation["snapshot"] = getattr(result, "confirmation_snapshot", None)
    update_message = getattr(result, "update_message", None)
    if update_message:
        confirmation["update_message"] = update_message
    else:
        confirmation.pop("update_message", None)
    task.payload.pop("transition_acknowledgment", None)
    task.payload.pop("previous_confirmation_snapshot", None)


def _handle_transaction_outcome(
    task: TaskSpec,
    task_id: str,
    result: Any,
    accumulator: ExecutionAccumulator,
    *,
    confirmation_gate: str,
    default_error: str | None,
) -> None:
    if result.outcome == TransactionOutcome.OK:
        task.stage = TaskStage.COMPLETED
        if result.receipt:
            task.payload["receipt"] = result.receipt

    elif result.outcome == TransactionOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        accumulator.add_missing_fields(task_id, result.required_fields)
        accumulator.add_details(task_id, result.details)
        accumulator.add_prompt(result.prompt, task_id)
        if result.update_message:
            accumulator.feedback_messages.append(result.update_message)

        if hint := result.patch.get("source_bank_name"):
            accumulator.source_bank_hints.append(hint)

    elif result.outcome == TransactionOutcome.NEEDS_CONFIRMATION:
        task.stage = TaskStage.AWAITING_CONFIRMATION
        accumulator.needs_confirm_tasks.append(task_id)
        _set_confirmation(task, result, gate_on=confirmation_gate)

    elif result.outcome == TransactionOutcome.NEEDS_AUTH:
        task.stage = TaskStage.AWAITING_AUTH
        accumulator.needs_auth_tasks.append(task_id)
        _set_confirmation(task, result, gate_on=confirmation_gate)

    elif result.outcome == TransactionOutcome.FAILED:
        task.stage = TaskStage.FAILED
        if default_error is None:
            task.payload["error"] = result.error
        else:
            task.payload["error"] = result.error or default_error
