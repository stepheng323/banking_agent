from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.referents.resolution import build_resolved_referents
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.async_grouping import _stamp_async_group_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.beneficiary_resolution import (
    _beneficiary_cache_contains_recipient,
    _message_mentions_compact_saved_alias,
    _normalize_beneficiary_rows,
    _recipient_supports_targeted_beneficiary_lookup,
)
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_frames import push_schedule_list_frame
from apps.chat.src.agent.orchestrator.workflows.execution.context_surface import context_surface
from apps.chat.src.agent.orchestrator.workflows.execution.funding.batch_funding_demands import (
    _batch_transfer_task_ids_for_wave,
    _batch_transfer_tasks_not_ready_for_funding,
    _recipient_ready_for_funding,
)
from apps.chat.src.agent.orchestrator.workflows.execution.last_interrupt import last_interrupt
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import (
    loaded_context,
    set_loaded_context_value,
)
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.recipient_review import recipient_review_signature
from apps.chat.src.agent.orchestrator.workflows.execution.result_reducer import (
    _apply_result_patch,
    _handle_transaction_outcome,
)
from apps.chat.src.agent.orchestrator.workflows.execution.session_stack import (
    SessionState,
    pop_active_session,
    upsert_active_session,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import get_task
from apps.chat.src.agent.orchestrator.workflows.execution.task_input import _maybe_user_message
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import (
    remove_task_payload_values,
    update_task_payload,
)
from apps.chat.src.agent.orchestrator.workflows.execution.turn_metadata import turn_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.worker_lookup import _get_worker
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.shared.schedule_management import format_schedule_context_blocks
from banking.transfers.funding.plan_validation import FUNDING_ADJUSTMENT_REVIEW_STATE
from banking.transfers.resolution.names import normalize_name
from shared.messaging.body_blocks import MessageDocument
from shared.utils.logging import get_logger, log_orchestrator_diagnostic

logger = get_logger(__name__)

_CONFIRMED_SNAPSHOT_FIELD_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("amount", ("amount",)),
    ("recipient_account", ("recipient_account", "recipientAccount")),
    ("recipient_bank_name", ("recipient_bank", "recipientBank", "recipient_bank_name")),
    ("source_bank_name", ("sourceBank", "source_bank", "source_bank_name")),
    ("source_account_number", ("sourceAccount", "source_account", "source_account_number")),
    ("authored_narration", ("authored_narration",)),
    ("narration", ("narration",)),
    ("description", ("description",)),
    ("user_note", ("user_note",)),
)
_RECIPIENT_INPUT_FIELDS = {"recipient_account", "recipient_bank_name"}
_FUNDING_ARTIFACT_PATCH_KEYS = {"funding_plan", "suggested_funding_plan"}
_FUNDING_ADJUSTMENT_FIELDS = {
    "amount",
    "explicit_split",
    "funding_plan",
    "source_accounts",
    "suggested_funding_plan",
}
_LOG_KEY_LIMIT = 40


class TransferTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_transfer_task(task, task_id, ctx)


class ScheduleTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_schedule_task(task, task_id, ctx)


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _confirmed_snapshot(
    task: TaskSpec,
    *,
    allow_auth_snapshot: bool = False,
) -> dict[str, Any] | None:
    confirmation = task.payload.get("confirmation")
    if not isinstance(confirmation, dict):
        return None
    if not confirmation.get("confirmed") and not (
        allow_auth_snapshot and task.stage in {TaskStage.AWAITING_AUTH, TaskStage.EXECUTING}
    ):
        return None
    snapshot = confirmation.get("snapshot")
    return snapshot if isinstance(snapshot, dict) and snapshot else None


def _snapshot_value(snapshot: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = snapshot.get(key)
        if _present(value):
            return value
    return None


def _resolve_snapshot_source_account_id(
    *,
    snapshot: dict[str, Any],
    accounts: list[dict[str, Any]],
) -> str | None:
    source_account_number = _text(
        _snapshot_value(snapshot, ("sourceAccount", "source_account", "source_account_number"))
    )
    if not source_account_number:
        return None

    source_bank = _text(_snapshot_value(snapshot, ("sourceBank", "source_bank", "source_bank_name")))
    matched_ids: list[str] = []
    for account in accounts:
        account_number = _text(
            account.get("account_number") or account.get("number") or account.get("source_account_number")
        )
        if account_number != source_account_number:
            continue
        if source_bank:
            account_bank = _text(account.get("bank_name") or account.get("bank"))
            if account_bank and account_bank.casefold() != source_bank.casefold():
                continue
        account_id = _text(account.get("id") or account.get("account_id"))
        if account_id:
            matched_ids.append(account_id)

    if len(matched_ids) != 1:
        return None
    return matched_ids[0]


def _rehydrate_transfer_payload_from_confirmed_snapshot(
    *,
    task: TaskSpec,
    pin_verified: bool,
    accounts: list[dict[str, Any]],
) -> list[str]:
    if not pin_verified:
        return []
    snapshot = _confirmed_snapshot(task, allow_auth_snapshot=pin_verified)
    if snapshot is None:
        return []

    payload_updates: dict[str, Any] = {}
    patched_fields: list[str] = []
    for payload_key, snapshot_keys in _CONFIRMED_SNAPSHOT_FIELD_MAP:
        if _present(task.payload.get(payload_key)):
            continue
        value = _snapshot_value(snapshot, snapshot_keys)
        if _present(value):
            payload_updates[payload_key] = value
            patched_fields.append(payload_key)

    if not _present(task.payload.get("source_account_id")):
        source_account_id = _resolve_snapshot_source_account_id(snapshot=snapshot, accounts=accounts)
        if source_account_id:
            payload_updates["source_account_id"] = source_account_id
            patched_fields.append("source_account_id")

    if payload_updates:
        update_task_payload(task, payload_updates)

    return patched_fields


def _confirmed_snapshot_has_recipient_details(task: TaskSpec, *, allow_auth_snapshot: bool = False) -> bool:
    snapshot = _confirmed_snapshot(task, allow_auth_snapshot=allow_auth_snapshot)
    if snapshot is None:
        return False
    return bool(
        _present(_snapshot_value(snapshot, ("recipient_account", "recipientAccount")))
        and _present(_snapshot_value(snapshot, ("recipient_bank", "recipientBank", "recipient_bank_name")))
    )


def _log_post_pin_recipient_prompt_if_snapshot_was_complete(
    *,
    task: TaskSpec,
    task_id: str,
    result: TransactionResult,
    pin_verified: bool,
) -> None:
    if not pin_verified or result.outcome != TransactionOutcome.NEEDS_INPUT:
        return
    required_fields = set(result.required_fields or [])
    if not required_fields.intersection(_RECIPIENT_INPUT_FIELDS):
        return
    if not _confirmed_snapshot_has_recipient_details(task, allow_auth_snapshot=pin_verified):
        return

    logger.warning(
        "post_pin_transfer_recipient_input_after_confirmed_snapshot",
        task_id=task_id,
        required_fields=sorted(required_fields),
        top_level_has_recipient_account=_present(task.payload.get("recipient_account")),
        top_level_has_recipient_bank=_present(task.payload.get("recipient_bank_name")),
    )


def _limited_keys(mapping: dict[str, Any] | None, *, limit: int = _LOG_KEY_LIMIT) -> list[str]:
    if not mapping:
        return []
    keys = sorted(str(key) for key in mapping)
    if len(keys) <= limit:
        return keys
    return [*keys[:limit], f"...+{len(keys) - limit} more"]


def _transfer_payload_log_summary(payload: dict[str, Any]) -> dict[str, Any]:
    confirmation = payload.get("confirmation")
    confirmation_dict = confirmation if isinstance(confirmation, dict) else {}
    funding_plan = payload.get("funding_plan")
    funding_plan_dict = funding_plan if isinstance(funding_plan, dict) else {}
    beneficiary_candidates = payload.get("beneficiary_candidates")
    referent_candidates = payload.get("referent_recipient_candidates")
    return {
        "action": payload.get("action"),
        "payload_key_count": len(payload),
        "payload_keys": _limited_keys(payload),
        "has_amount": _present(payload.get("amount")),
        "has_recipient_name": _present(payload.get("recipient_name")),
        "has_recipient_account": _present(payload.get("recipient_account")),
        "has_recipient_bank": _present(payload.get("recipient_bank_name")),
        "has_source_account_id": _present(payload.get("source_account_id")),
        "has_source_bank": _present(payload.get("source_bank_name")),
        "skip_extraction": payload.get("skip_extraction"),
        "confirmation_confirmed": bool(confirmation_dict.get("confirmed")),
        "confirmation_has_snapshot": isinstance(confirmation_dict.get("snapshot"), dict),
        "funding_plan_present": bool(funding_plan_dict),
        "funding_step_count": len(funding_plan_dict.get("steps") or []) if funding_plan_dict else 0,
        "async_group_kind": payload.get("async_group_kind"),
        "async_group_size": payload.get("async_group_size"),
        "async_group_index": payload.get("async_group_index"),
        "beneficiary_candidate_count": len(beneficiary_candidates) if isinstance(beneficiary_candidates, list) else 0,
        "referent_candidate_count": len(referent_candidates) if isinstance(referent_candidates, list) else 0,
        "schedule_mode": payload.get("schedule_mode"),
    }


def _has_transfer_destination(payload: dict[str, Any]) -> bool:
    return bool(
        _present(payload.get("recipient_account") or payload.get("recipient_account_number"))
        and _present(payload.get("recipient_bank_name") or payload.get("recipient_bank_code"))
    )


def _recipient_resolution_patch_present(patch: dict[str, Any]) -> bool:
    return bool(
        _present(patch.get("recipient_resolved_name"))
        or _present(patch.get("recipient_reference"))
        or _present(patch.get("recipient_resolution_provider"))
    )


def _casefold_text(value: Any) -> str:
    return str(value or "").strip().casefold()


def _recipient_identity_equivalent(previous_payload: dict[str, Any], current_payload: dict[str, Any]) -> bool:
    previous_account = _casefold_text(
        previous_payload.get("recipient_account") or previous_payload.get("recipient_account_number")
    )
    current_account = _casefold_text(
        current_payload.get("recipient_account") or current_payload.get("recipient_account_number")
    )
    if not previous_account or previous_account != current_account:
        return False

    previous_bank = normalize_name(
        previous_payload.get("recipient_bank_name") or previous_payload.get("recipient_bank_code")
    )
    current_bank = normalize_name(
        current_payload.get("recipient_bank_name") or current_payload.get("recipient_bank_code")
    )
    if not previous_bank or previous_bank != current_bank:
        return False

    previous_name = normalize_name(
        previous_payload.get("recipient_resolved_name") or previous_payload.get("recipient_name")
    )
    current_name = normalize_name(
        current_payload.get("recipient_resolved_name") or current_payload.get("recipient_name")
    )
    return bool(previous_name and previous_name == current_name)


def _maybe_mark_recipient_review_required(
    *,
    task: TaskSpec,
    required_fields: list[str],
    result_patch: dict[str, Any],
    previous_payload: dict[str, Any],
    previous_signature: str | None,
) -> bool:
    if task.type != "transfer" or not result_patch:
        return False
    if not _recipient_resolution_patch_present(result_patch):
        return False
    signature = recipient_review_signature(task.payload)
    if signature is None or signature == previous_signature:
        return False
    if (
        previous_signature
        and previous_payload.get("recipient_review_confirmed")
        and _recipient_identity_equivalent(previous_payload, task.payload)
    ):
        update_task_payload(
            task,
            {
                "recipient_review_required": False,
                "recipient_review_confirmed": True,
                "recipient_review_signature": signature,
            },
        )
        logger.info("recipient_review_signature_carried_forward", task_id=task.id)
        return False

    recipient_input_fields = {"recipient_account", "recipient_bank_name"}
    was_recipient_input_turn = bool(set(required_fields) & recipient_input_fields)

    from_saved = bool(task.payload.get("resolved_from_saved_beneficiary"))
    formal_mismatch = bool(task.payload.get("name_mismatch"))

    if from_saved:
        alias_mismatch = formal_mismatch
    else:
        alias = _casefold_text(task.payload.get("recipient_name"))
        resolved = _casefold_text(task.payload.get("recipient_resolved_name"))
        alias_mismatch = bool(alias and resolved and alias != resolved)

    if not was_recipient_input_turn and not alias_mismatch:
        return False

    update_task_payload(
        task,
        {
            "recipient_review_required": True,
            "recipient_review_confirmed": False,
        },
    )
    remove_task_payload_values(task, "recipient_review_signature")
    return True


def _beneficiary_candidates_need_hydration(payload: dict[str, Any]) -> bool:
    if _has_transfer_destination(payload):
        return False
    candidates = payload.get("beneficiary_candidates")
    if not isinstance(candidates, list) or not candidates:
        return False
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return True
        if not _present(candidate.get("beneficiary_id")):
            continue
        if not _present(candidate.get("recipient_account")) or not (
            _present(candidate.get("recipient_bank_name")) or _present(candidate.get("recipient_bank_code"))
        ):
            return True
    return False


def _transfer_result_log_summary(result: TransactionResult) -> dict[str, Any]:
    return {
        "outcome": result.outcome,
        "has_receipt": bool(result.receipt),
        "has_response": bool(result.response),
        "has_prompt": bool(result.prompt),
        "required_fields": result.required_fields,
        "patch_key_count": len(result.patch),
        "patch_keys": _limited_keys(result.patch),
        "details_keys": _limited_keys(result.details),
        "has_confirmation_snapshot": bool(result.confirmation_snapshot),
        "has_confirmation_summary": bool(result.confirmation_summary),
        "has_update_message": bool(result.update_message),
        "has_error": bool(result.error),
        "retryable": result.retryable,
        "patch_skip_ext": result.patch.get("skip_extraction") if result.patch else None,
    }


def _is_funding_adjustment_result(result: TransactionResult) -> bool:
    if result.outcome != TransactionOutcome.NEEDS_INPUT:
        return False
    details = result.details if isinstance(result.details, dict) else {}
    return details.get("review_state") == FUNDING_ADJUSTMENT_REVIEW_STATE


def _active_batch_task_ids_for_transfer(task_id: str, ctx: ExecutionTurnContext) -> list[str]:
    current_wave = ctx.current_wave_task_ids or []
    return _batch_transfer_task_ids_for_wave(ctx.state, current_wave, task_id=task_id)


def _batch_not_ready_task_ids_for_transfer(task_id: str, ctx: ExecutionTurnContext) -> list[str]:
    current_wave = ctx.current_wave_task_ids or []
    return _batch_transfer_tasks_not_ready_for_funding(ctx.state, current_wave, task_id=task_id)


def _result_makes_current_transfer_ready_for_batch(
    *,
    task_id: str,
    result: TransactionResult,
    ctx: ExecutionTurnContext,
) -> bool:
    task = get_task(ctx.state, task_id)
    payload = dict(task.payload) if task and isinstance(task.payload, dict) else {}
    if isinstance(result.patch, dict):
        payload.update(result.patch)

    snapshot = result.confirmation_snapshot if isinstance(result.confirmation_snapshot, dict) else {}
    if snapshot:
        if not payload.get("recipient_account"):
            payload["recipient_account"] = snapshot.get("recipient_account") or snapshot.get("recipientAccount")
        if not payload.get("recipient_bank_name") and not payload.get("recipient_bank_code"):
            payload["recipient_bank_name"] = snapshot.get("recipient_bank") or snapshot.get("recipientBank")
        if not payload.get("recipient_resolved_name"):
            payload["recipient_resolved_name"] = snapshot.get("recipient_name") or snapshot.get("recipientName")

    return _recipient_ready_for_funding(payload)


def _should_suppress_single_leg_batch_blocker(
    *,
    task_id: str,
    result: TransactionResult,
    ctx: ExecutionTurnContext,
) -> tuple[bool, list[str]]:
    batch_task_ids = _active_batch_task_ids_for_transfer(task_id, ctx)
    if len(batch_task_ids) < 2:
        return False, []

    if _is_funding_adjustment_result(result):
        return True, _batch_not_ready_task_ids_for_transfer(task_id, ctx)

    if result.outcome in {TransactionOutcome.NEEDS_CONFIRMATION, TransactionOutcome.NEEDS_AUTH}:
        waiting_task_ids = _batch_not_ready_task_ids_for_transfer(task_id, ctx)
        if task_id in waiting_task_ids and _result_makes_current_transfer_ready_for_batch(
            task_id=task_id,
            result=result,
            ctx=ctx,
        ):
            waiting_task_ids = [candidate_id for candidate_id in waiting_task_ids if candidate_id != task_id]
        return bool(waiting_task_ids), waiting_task_ids

    return False, []


def _strip_single_leg_funding_artifacts(result: TransactionResult) -> TransactionResult:
    patch = {key: value for key, value in result.patch.items() if key not in _FUNDING_ARTIFACT_PATCH_KEYS}
    required_fields = [field for field in result.required_fields if field not in _FUNDING_ADJUSTMENT_FIELDS]
    return result.model_copy(
        update={
            "patch": patch,
            "required_fields": required_fields,
            "prompt": None,
            "details": {},
            "update_message": None,
        }
    )


async def _execute_transfer_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    worker = _get_worker(
        ctx.services,
        "transfer",
        task,
        log_key="transfer_worker_missing",
        error_message=render_message("orchestrator.error.transfer_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    required_fields: list[str] = []
    previous_response: str | None = None
    confirmation_task_count: int | None = None
    interrupt = last_interrupt(ctx.state)
    if interrupt.includes_task(task_id):
        required_fields = interrupt.fields_for_task(task_id)
        previous_response = interrupt.prompt
        if interrupt.is_kind("confirmation"):
            confirmation_task_count = interrupt.task_count

    user_msg = _maybe_user_message(task, ctx.state)

    context = loaded_context(ctx.state)
    surface = context_surface(ctx.state)
    turn = turn_metadata(ctx.state)
    rehydrated_fields = _rehydrate_transfer_payload_from_confirmed_snapshot(
        task=task,
        pin_verified=turn.pin_verified,
        accounts=context.transaction_account_rows_or_account_rows,
    )
    if rehydrated_fields:
        logger.info(
            "post_pin_transfer_payload_rehydrated_from_confirmed_snapshot",
            task_id=task_id,
            fields=rehydrated_fields,
        )

    awaiting_raw_slot_input = bool(required_fields)
    needs_account_selection = not task.payload.get("source_account_id")
    if (
        (not user_msg or not user_msg.strip())
        and task.payload.get("recipient_name")
        and not needs_account_selection
        and not awaiting_raw_slot_input
    ):
        r_name = task.payload["recipient_name"]
        if isinstance(r_name, str) and r_name.lower() not in ("him", "her", "them", "that", "it", "this", "previous"):
            amt = task.payload.get("amount") or ""
            user_msg = f"Send {amt} to {r_name}"
            logger.info("user_msg_synthesized", msg=user_msg)

    beneficiaries = context.beneficiaries

    recipient_name = task.payload.get("recipient_name")
    has_recipient_hint = isinstance(recipient_name, str) and bool(recipient_name.strip())
    recipient_name_text = recipient_name.strip() if isinstance(recipient_name, str) else ""
    beneficiary_repo = ctx.dependencies.beneficiary_repo
    user_id = context.user_id
    beneficiary_context_mode = context.beneficiary_context_mode
    hydrate_beneficiary_candidates = _beneficiary_candidates_need_hydration(task.payload)
    hydrate_compact_alias = _message_mentions_compact_saved_alias(user_msg, beneficiaries)
    if (
        beneficiary_repo
        and user_id
        and (
            not beneficiaries
            or hydrate_beneficiary_candidates
            or hydrate_compact_alias
            or (
                has_recipient_hint
                and beneficiary_context_mode == "cache_only"
                and _recipient_supports_targeted_beneficiary_lookup(recipient_name_text)
                and not _beneficiary_cache_contains_recipient(beneficiaries, recipient_name_text)
            )
        )
    ):
        try:
            fetched_rows: list[Any] = []
            reload_mode = "full"
            if (
                not hydrate_beneficiary_candidates
                and has_recipient_hint
                and beneficiary_context_mode == "cache_only"
                and _recipient_supports_targeted_beneficiary_lookup(recipient_name_text)
                and hasattr(beneficiary_repo, "search_by_name")
            ):
                fetched_rows = await beneficiary_repo.search_by_name(
                    str(user_id),
                    recipient_name_text,
                    beneficiary_type="transfer",
                )
                reload_mode = "targeted"

            if not fetched_rows:
                fetched_rows = await beneficiary_repo.get_by_user(str(user_id), beneficiary_type="transfer")
                if reload_mode == "targeted":
                    reload_mode = "targeted_fallback_full"
                elif hydrate_beneficiary_candidates:
                    reload_mode = "candidate_selection_hydration"
                elif hydrate_compact_alias:
                    reload_mode = "compact_alias_hydration"
                elif beneficiary_context_mode == "cache_only" and not has_recipient_hint:
                    reload_mode = "full_no_recipient_hint"
                else:
                    reload_mode = "full"

            beneficiaries = _normalize_beneficiary_rows(fetched_rows if isinstance(fetched_rows, list) else [])
            set_loaded_context_value(ctx.state, "beneficiaries", beneficiaries)
            logger.info(
                "transfer_beneficiaries_reloaded_for_resolution",
                user_id=str(user_id),
                fetched_count=len(beneficiaries),
                reload_mode=reload_mode,
            )
        except Exception as e:
            logger.warning(
                "transfer_beneficiary_reload_failed",
                user_id=str(user_id),
                error=str(e),
            )

    resolved_referents = build_resolved_referents(ctx.state, user_msg)
    context_data = {
        "phone_number": turn.phone_number,
        "channel": turn.channel,
        "channel_identity": turn.channel_identity,
        "user_id": context.user_id,
        "accounts": context.transaction_accounts_or_accounts,
        "all_accounts": context.accounts,
        "beneficiaries": beneficiaries,
        "referent_memory": surface.referent_memory_payload(),
        "resolved_referents": resolved_referents,
        "language": _state_locale(ctx.state),
        "required_fields": required_fields,
        "previous_response": previous_response,
        "confirmation_task_count": confirmation_task_count,
        "authorization_context": turn.authorization_context_payload,
        "progress_tracker": ctx.dependencies.progress_tracker,
        "stashed_sessions": turn.stashed_sessions,
    }
    _stamp_async_group_metadata(task, ctx)
    if task.payload.get("source_affinity_mode") is None:
        remove_task_payload_values(task, "source_affinity_mode")

    previous_recipient_payload = dict(task.payload)
    previous_recipient_review_signature = recipient_review_signature(task.payload)

    log_orchestrator_diagnostic(
        logger,
        "transfer_worker_start",
        task_id=task_id,
        **_transfer_payload_log_summary(task.payload),
    )
    result = cast(
        TransactionResult,
        await worker.run(
            payload=task.payload,
            context=context_data,
            user_message=user_msg,
            pin_verified=turn.pin_verified,
        ),
    )
    log_orchestrator_diagnostic(
        logger,
        "transfer_worker_returned",
        task_id=task_id,
        **_transfer_result_log_summary(result),
    )
    _log_post_pin_recipient_prompt_if_snapshot_was_complete(
        task=task,
        task_id=task_id,
        result=result,
        pin_verified=turn.pin_verified,
    )

    suppress_single_leg_batch_blocker, waiting_batch_task_ids = _should_suppress_single_leg_batch_blocker(
        task_id=task_id,
        result=result,
        ctx=ctx,
    )
    if suppress_single_leg_batch_blocker:
        logger.info(
            "batch_transfer_single_leg_blocker_suppressed",
            task_id=task_id,
            outcome=result.outcome,
            funding_adjustment=_is_funding_adjustment_result(result),
            waiting_task_count=len(waiting_batch_task_ids),
        )
        result = _strip_single_leg_funding_artifacts(result)

    _apply_result_patch(task, result)
    recipient_review_required = False
    if isinstance(result.patch, dict):
        recipient_review_required = _maybe_mark_recipient_review_required(
            task=task,
            required_fields=required_fields,
            result_patch=result.patch,
            previous_payload=previous_recipient_payload,
            previous_signature=previous_recipient_review_signature,
        )
        schedule_items = result.patch.get("schedule_context_items")
        if isinstance(schedule_items, list):
            push_schedule_list_frame(ctx, [item for item in schedule_items if isinstance(item, dict)])
    if result.response and not recipient_review_required and not suppress_single_leg_batch_blocker:
        ctx.accumulator.say(result.response)

    if result.outcome == TransactionOutcome.OK and result.receipt:
        logger.info("transfer_worker_ok_branch", has_receipt=bool(result.receipt))

    defer_confirmation_for_recipient_review = (
        recipient_review_required and result.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    )
    if not defer_confirmation_for_recipient_review and not suppress_single_leg_batch_blocker:
        _handle_transaction_outcome(
            task,
            task_id,
            result,
            ctx.accumulator,
            confirmation_gate="snapshot",
            default_error=None,
        )

    if (
        result.outcome
        in (
            TransactionOutcome.NEEDS_INPUT,
            TransactionOutcome.NEEDS_AUTH,
            TransactionOutcome.NEEDS_CONFIRMATION,
        )
        and not defer_confirmation_for_recipient_review
        and not suppress_single_leg_batch_blocker
    ):
        state_map: dict[TransactionOutcome, SessionState] = {
            TransactionOutcome.NEEDS_INPUT: "WAITING_FOR_INPUT",
            TransactionOutcome.NEEDS_AUTH: "WAITING_FOR_AUTH",
            TransactionOutcome.NEEDS_CONFIRMATION: "WAITING_FOR_INPUT",
        }
        current_state: SessionState = state_map[result.outcome]

        upsert_active_session(
            ctx,
            domain="transfer",
            state=current_state,
            interrupt_policy="BLOCK" if result.outcome == TransactionOutcome.NEEDS_AUTH else "CONFIRM",
            task_id=task_id,
        )

    elif result.outcome in (TransactionOutcome.OK, TransactionOutcome.FAILED) and result.is_terminal:
        pop_active_session(ctx, domain="transfer")


async def _execute_schedule_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    """Run scheduled transaction management through the transfer scheduler worker.

    Schedule management is planner-owned and can be read-only. It must not pass
    through the transfer mandate gate just because the implementation currently
    lives on the transfer worker.
    """

    worker = _get_worker(
        ctx.services,
        "transfer",
        task,
        log_key="schedule_worker_missing",
        error_message=render_message("orchestrator.error.transfer_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    context = loaded_context(ctx.state)
    turn = turn_metadata(ctx.state)
    context_data = {
        "phone_number": turn.phone_number,
        "channel": turn.channel,
        "channel_identity": turn.channel_identity,
        "user_id": context.user_id,
        "accounts": context.accounts,
        "all_accounts": context.accounts,
        "beneficiaries": context.beneficiaries,
        "language": _state_locale(ctx.state),
        "required_fields": [],
        "previous_response": None,
        "confirmation_task_count": None,
        "progress_tracker": ctx.dependencies.progress_tracker,
        "stashed_sessions": turn.stashed_sessions,
    }
    user_msg = _maybe_user_message(task, ctx.state)
    logger.info("schedule_worker_start", payload=task.payload, task_id=task_id)
    result = cast(
        TransactionResult,
        await worker.run(
            payload=task.payload,
            context=context_data,
            user_message=user_msg,
            pin_verified=turn.pin_verified,
        ),
    )
    logger.info("schedule_worker_returned", outcome=result.outcome, task_id=task_id)

    _apply_result_patch(task, result)
    if isinstance(result.patch, dict):
        schedule_items = result.patch.get("schedule_context_items")
        if isinstance(schedule_items, list):
            push_schedule_list_frame(ctx, [item for item in schedule_items if isinstance(item, dict)])
    if result.response:
        body_blocks = _schedule_response_body_blocks(task, result, locale=_state_locale(ctx.state))
        if body_blocks:
            ctx.accumulator.add_outbox({"type": "say", "text": result.response, "body_blocks": body_blocks})
        else:
            ctx.accumulator.say(result.response)

    _handle_transaction_outcome(
        task,
        task_id,
        result,
        ctx.accumulator,
        confirmation_gate="snapshot",
        default_error=None,
    )


def _schedule_response_body_blocks(
    task: TaskSpec,
    result: TransactionResult,
    *,
    locale: str,
) -> MessageDocument | None:
    if not isinstance(result.patch, dict):
        return None
    if str(task.payload.get("schedule_response_mode") or "").strip().lower() == "count":
        return None

    schedule_items = result.patch.get("schedule_context_items")
    if not isinstance(schedule_items, list):
        return None
    items = [item for item in schedule_items if isinstance(item, dict)]
    if not items:
        return None

    action = str(task.payload.get("action") or "").strip().lower()
    heading = (
        render_message("schedule.find.header", locale)
        if action == "find_scheduled_transaction"
        else render_message("schedule.list.header", locale)
    )
    return format_schedule_context_blocks(items, heading=heading)


__all__ = ["ScheduleTaskExecutor", "TransferTaskExecutor"]
