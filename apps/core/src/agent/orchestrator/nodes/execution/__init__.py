import re
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.execution.handlers import (
    ExecutionAggregation,
    ExecutionContext,
    handle_account_task,
    handle_airtime_task,
    handle_beneficiary_task,
    handle_data_task,
    handle_faq_task,
    handle_orchestrator_task,
    handle_query_task,
    handle_support_task,
    handle_transfer_task,
)
from apps.core.src.agent.orchestrator.models.domain import (
    PendingInterrupt,
    TaskStage,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload
from shared.formatters.accounts import (
    format_accounts_list,
)
from shared.formatters.confirmation import (
    append_source_account_info,
    build_confirmation_summary,
    build_source_account_info,
    strip_source_account_info_lines,
)
from shared.formatters.prompts import (
    format_auth_reason,
    format_batch_transfer_source_prompt,
    format_missing_details_prompt,
    format_single_transfer_recipient_prompt,
    format_source_repair_prompt,
)
from shared.formatters.recipient_display import format_recipient_display_label
from shared.formatters.transaction_copy import build_confirmation_header
from shared.formatters.transaction_summary import format_batch_transfer_summary, format_intent_line
from shared.i18n import render_message
from shared.services.funding.coordinator import BatchFundingCoordinator, SourceAffinity, TransferDemand
from shared.services.onboarding.mandate_messages import build_pending_mandate_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TERMINAL_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}
BLOCKING_DEPENDENCY_STAGES = {TaskStage.FAILED, TaskStage.CANCELLED}
EXECUTION_ONLY_FIELDS = {"source_account_id", "pin", "confirmation_summary"}
INPUT_MUTABLE_STAGES = {
    TaskStage.DRAFT,
    TaskStage.EXTRACTED,
    TaskStage.RESOLVED,
    TaskStage.VALIDATED,
}
TRANSACTION_TASK_TYPES = {"transfer", "airtime", "data"}


def _build_mandate_gate_error(accounts: list[dict], locale: str) -> str:
    """Build mandate error using contextual transfer destinations when available."""
    normalized_accounts = [account for account in accounts if isinstance(account, dict)]
    return build_pending_mandate_message(normalized_accounts, locale)


def _with_policy_notice(state: OrchestratorState, outbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepend policy notice once per turn when present."""
    if not state.policy_notice:
        return outbox
    logger.info("policy_notice_injected")
    return [{"type": "say", "text": state.policy_notice}, *outbox]


def _dedupe_task_ids(task_ids: list[str], current_wave: list[str]) -> list[str]:
    """Deduplicate task ids while preserving current-wave order."""
    requested = [task_id for task_id in task_ids if task_id]
    if not requested:
        return []

    requested_set = set(requested)
    seen: set[str] = set()
    ordered: list[str] = []

    for task_id in current_wave:
        if task_id in requested_set and task_id not in seen:
            ordered.append(task_id)
            seen.add(task_id)


    for task_id in requested:
        if task_id not in seen:
            ordered.append(task_id)
            seen.add(task_id)

    return ordered


def _dependency_resolution(task: Any, all_tasks: dict[str, Any]) -> tuple[str, str | None]:
    """Resolve whether task dependencies are ready, waiting, or failed."""
    depends_on = task.depends_on if hasattr(task, "depends_on") else []
    for dep_id in depends_on:
        dep_task = all_tasks.get(dep_id)
        if not dep_task:
            continue
        if dep_task.stage in BLOCKING_DEPENDENCY_STAGES:
            return "cancel", dep_id
        if dep_task.stage != TaskStage.COMPLETED:
            return "wait", dep_id
    return "ready", None


def _build_show_options_entry(
    *,
    details: dict[str, Any] | None,
    prompt_text: str,
    task_id: str,
    focused_missing_fields: list[str],
) -> dict[str, Any] | None:
    if not isinstance(details, dict):
        return None

    options: list[dict[str, str]] = []
    raw_options = details.get("options")
    if isinstance(raw_options, list):
        for idx, option in enumerate(raw_options, start=1):
            if not isinstance(option, dict):
                continue
            option_id = str(option.get("id", "")).strip() or str(idx)
            title = option.get("title") or option.get("label") or f"Option {idx}"
            options.append({"id": option_id, "title": str(title)})

    if not options and "beneficiary_id" in focused_missing_fields:
        raw_candidates = details.get("candidates")
        if isinstance(raw_candidates, list):
            for idx, candidate in enumerate(raw_candidates, start=1):
                if not isinstance(candidate, dict):
                    continue
                option_id = (
                    str(candidate.get("option_id", "")).strip()
                    or str(candidate.get("beneficiary_id", "")).strip()
                    or str(candidate.get("id", "")).strip()
                    or str(idx)
                )
                label = candidate.get("label") or candidate.get("title") or f"Option {idx}"
                options.append({"id": option_id, "title": str(label)})

    if not options:
        return None

    logger.info("option_prompt_emitted", task_id=task_id, option_count=len(options))
    return {
        "type": "show_options",
        "title": prompt_text,
        "task_ids": [task_id],
        "options": options,
    }


def _compact_prompt_for_options(prompt_text: str) -> str:
    """Strip duplicated numbered option lines when native option UI is available."""
    lines = prompt_text.splitlines()
    compact_lines = [line for line in lines if not re.match(r"^\s*\d+[\).\s-]+", line)]
    deduped_lines: list[str] = []
    last_normalized = ""
    for line in compact_lines:
        normalized = re.sub(r"\s+", " ", line).strip().lower()
        if normalized and normalized == last_normalized:
            continue
        deduped_lines.append(line)
        if normalized:
            last_normalized = normalized
    compact = "\n".join(deduped_lines).strip()
    return compact or prompt_text


def _recipient_prompt_label(task_payload: dict[str, Any]) -> str | None:
    """Build recipient display label for prompts: alias first, resolved name in parentheses."""
    recipient_name = task_payload.get("recipient_name")
    resolved_name = task_payload.get("recipient_resolved_name")
    return format_recipient_display_label(
        str(recipient_name) if isinstance(recipient_name, str) else None,
        str(resolved_name) if isinstance(resolved_name, str) else None,
    )


def _strip_batch_name_mismatch_warning(summary: str, task_payload: dict[str, Any]) -> str:
    """Remove verbose name-mismatch warning from batch confirmations while preserving the core summary."""
    warning = task_payload.get("name_mismatch_warning")
    if not isinstance(warning, str) or not warning.strip():
        return summary

    cleaned = summary.strip()
    warning_text = warning.strip()
    if not cleaned:
        return ""

    if cleaned == warning_text:
        return ""

    for pattern in (f"{warning_text}\n\n", f"{warning_text}\n"):
        if cleaned.startswith(pattern):
            return cleaned[len(pattern) :].strip()

    if cleaned.startswith(warning_text):
        return cleaned[len(warning_text) :].lstrip("\n").strip()

    return cleaned


def _render_task_confirmation_summary(
    *,
    task: Any,
    locale: str,
    accounts: list[dict[str, Any]],
) -> str:
    canonical = build_confirmation_summary(
        task_payload=task.payload,
        locale=locale,
        accounts=accounts,
    )
    if canonical:
        cleaned = _strip_batch_name_mismatch_warning(canonical, task.payload)
        if cleaned:
            return cleaned

    confirmation_payload = task.payload.get("confirmation") or {}
    raw_summary = confirmation_payload.get("summary")
    if isinstance(raw_summary, str):
        cleaned = _strip_batch_name_mismatch_warning(raw_summary, task.payload)
        if cleaned:
            source_account_info = build_source_account_info(
                task_payload=task.payload,
                snapshot=confirmation_payload.get("snapshot")
                if isinstance(confirmation_payload.get("snapshot"), dict)
                else {},
                accounts=accounts,
                locale=locale,
            )
            return append_source_account_info(cleaned, source_account_info, locale=locale)

    return format_intent_line(task.type, task.payload, locale=locale)


def _build_confirmation_gate_summary(
    *,
    state: OrchestratorState,
    task_ids: list[str],
    locale: str,
    accounts: list[dict[str, Any]],
) -> str:
    if not task_ids:
        return ""

    if len(task_ids) == 1:
        task = state.tasks[task_ids[0]]
        return _render_task_confirmation_summary(task=task, locale=locale, accounts=accounts)

    task_types = {state.tasks[tid].type for tid in task_ids if tid in state.tasks}
    if task_types == {"transfer"}:
        total_amount = 0.0
        source_account_info: str | None = None
        summaries: list[str] = []
        for tid in task_ids:
            task = state.tasks[tid]
            confirmation_payload = task.payload.get("confirmation") or {}
            snapshot = confirmation_payload.get("snapshot") or {}
            if isinstance(snapshot, dict):
                amount = snapshot.get("amount", 0)
                if isinstance(amount, (int, float)):
                    total_amount += float(amount)
            if source_account_info is None:
                source_account_info = build_source_account_info(
                    task_payload=task.payload,
                    snapshot=snapshot if isinstance(snapshot, dict) else {},
                    accounts=accounts,
                    locale=locale,
                )
            raw_summary = confirmation_payload.get("summary")
            task_summary = ""
            if isinstance(raw_summary, str):
                task_summary = _strip_batch_name_mismatch_warning(raw_summary, task.payload)
            if not task_summary:
                task_summary = _render_task_confirmation_summary(task=task, locale=locale, accounts=accounts)
            task_summary = strip_source_account_info_lines(task_summary, locale=locale)
            if task_summary:
                summaries.append(task_summary)
        return format_batch_transfer_summary(
            num_transfers=len(task_ids),
            total_amount=total_amount,
            source_account_info=source_account_info,
            summaries=summaries,
            locale=locale,
        )

    summaries = [
        _render_task_confirmation_summary(task=state.tasks[tid], locale=locale, accounts=accounts)
        for tid in task_ids
        if tid in state.tasks
    ]
    non_empty = [summary for summary in summaries if summary]
    if not non_empty:
        return ""

    source_infos: list[str] = []
    for tid in task_ids:
        task = state.tasks.get(tid)
        if not task:
            continue
        confirmation_payload = task.payload.get("confirmation") or {}
        snapshot = confirmation_payload.get("snapshot")
        source_info = build_source_account_info(
            task_payload=task.payload,
            snapshot=snapshot if isinstance(snapshot, dict) else {},
            accounts=accounts,
            locale=locale,
        )
        if source_info:
            source_infos.append(source_info)

    if source_infos and len(set(source_infos)) == 1 and len(non_empty) > 1:
        stripped = [strip_source_account_info_lines(summary, locale=locale) for summary in non_empty]
        stripped_non_empty = [summary for summary in stripped if summary]
        merged = "\n\n".join(stripped_non_empty)
        return append_source_account_info(merged, source_infos[0], locale=locale)

    return "\n\n".join(non_empty)


def _compact_confirmation_update_message(update_messages: list[str], locale: str) -> str | None:
    normalized: list[str] = []
    seen: set[str] = set()
    for message in update_messages:
        compact = message.strip()
        if not compact:
            continue
        key = re.sub(r"\s+", " ", compact).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(compact)

    if not normalized:
        return None
    if len(normalized) == 1:
        return normalized[0]
    return render_message(
        "response.templates.acknowledge_change",
        locale,
        {"changes_text": "your transfer details"},
    )


def _auth_header_for_tasks(state: OrchestratorState, task_ids: list[str], *, locale: str) -> str:
    task_types = {state.tasks[task_id].type for task_id in task_ids if task_id in state.tasks}
    if len(task_types) == 1:
        return format_auth_reason(next(iter(task_types)), locale=locale)
    return format_auth_reason("mixed", locale=locale)


def _queued_transaction_tasks_for_focus(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    focused_tid: str,
) -> list[Any]:
    queued: list[Any] = []
    for tid in current_wave:
        if tid == focused_tid:
            continue
        task = state.tasks.get(tid)
        if not task or task.type not in TRANSACTION_TASK_TYPES:
            continue
        if task.stage in TERMINAL_STAGES:
            continue
        queued.append(task)
    return queued


def _append_queued_notice(
    *,
    prompt_text: str,
    queued_tasks: list[Any],
    locale: str,
) -> tuple[str, dict[str, Any] | None]:
    if not queued_tasks:
        return prompt_text, None

    queued_lines = [format_intent_line(task.type, task.payload, locale=locale) for task in queued_tasks]
    queued_lines = [line for line in queued_lines if line]
    if not queued_lines:
        return prompt_text, None

    queued_text = "\n".join(f"• {line}" for line in queued_lines)
    queue_notice = render_message(
        "orchestrator.execution.queued_next_notice",
        locale,
        {"queued_intents": queued_text},
    )
    merged_prompt = f"{prompt_text}\n\n{queue_notice}" if prompt_text else queue_notice
    queue_meta = {
        "queued_task_ids": [task.id for task in queued_tasks],
        "queued_task_types": [task.type for task in queued_tasks],
        "queued_intents": queued_lines,
    }
    return merged_prompt, queue_meta


def _build_planning_signature(task_payload: dict[str, Any]) -> dict[str, Any]:
    explicit_split_raw = task_payload.get("explicit_split")
    explicit_split = explicit_split_raw if isinstance(explicit_split_raw, dict) else {}
    normalized_split = {
        str(k): float(v)
        for k, v in sorted(explicit_split.items(), key=lambda item: str(item[0]))
        if isinstance(v, (int, float))
    }
    source_accounts_raw = task_payload.get("source_accounts")
    source_accounts = source_accounts_raw if isinstance(source_accounts_raw, list) else []
    return {
        "planned_for_amount": float(task_payload.get("amount") or 0.0),
        "planned_for_source_account_id": task_payload.get("source_account_id"),
        "planned_for_source_accounts": sorted([str(bank) for bank in source_accounts if str(bank).strip()]),
        "planned_for_use_dual_accounts": bool(task_payload.get("use_dual_accounts")),
        "planned_for_explicit_split": normalized_split,
    }


def _funding_plan_to_payload_dict(plan: Any, task_payload: dict[str, Any]) -> dict[str, Any]:
    signature = _build_planning_signature(task_payload)
    return {
        "transfer_amount": float(plan.transfer_amount),
        "total_funded": float(plan.total_funded),
        "is_sufficient": bool(plan.is_sufficient),
        "is_single_source": len(plan.steps) == 1,
        "trigger_mode": plan.trigger_mode,
        "requested_sources": list(plan.requested_sources),
        "explicit_split_applied": bool(plan.explicit_split_applied),
        "primary_account_id": str(plan.primary_account_id) if plan.primary_account_id else None,
        "primary_bank_name": plan.primary_bank_name,
        "primary_available_balance": plan.primary_available_balance,
        "planned_for_amount": signature["planned_for_amount"],
        "planned_for_source_account_id": signature["planned_for_source_account_id"],
        "planned_for_source_accounts": signature["planned_for_source_accounts"],
        "planned_for_use_dual_accounts": signature["planned_for_use_dual_accounts"],
        "planned_for_explicit_split": signature["planned_for_explicit_split"],
        "steps": [
            {
                "account_id": str(step.account_id),
                "amount": float(step.amount),
                "bank_name": step.bank_name,
                "sequence": int(step.sequence),
            }
            for step in plan.steps
        ],
    }


def _build_transfer_demand(task_id: str, task_payload: dict[str, Any]) -> TransferDemand:
    mode = "explicit" if task_payload.get("source_affinity_mode") == "explicit" else "auto"
    source_accounts_raw = task_payload.get("source_accounts")
    source_accounts = source_accounts_raw if isinstance(source_accounts_raw, list) else []
    explicit_sources = [str(bank) for bank in source_accounts if str(bank).strip()]
    source_bank_name = task_payload.get("source_bank_name")
    if mode == "explicit" and not explicit_sources and isinstance(source_bank_name, str) and source_bank_name.strip():
        explicit_sources = [source_bank_name.strip()]

    explicit_split_raw = task_payload.get("explicit_split")
    explicit_split = explicit_split_raw if isinstance(explicit_split_raw, dict) else None
    preferred_account_id = task_payload.get("source_account_id")
    return TransferDemand(
        task_id=task_id,
        amount=float(task_payload.get("amount") or 0.0),
        source_affinity=SourceAffinity(mode=cast(Any, mode)),
        explicit_sources=explicit_sources,
        explicit_split=cast(dict[str, float] | None, explicit_split),
        use_dual_accounts=bool(task_payload.get("use_dual_accounts")),
        preferred_account_id=str(preferred_account_id) if preferred_account_id else None,
    )


def _is_plannable_transfer_task(task: Any) -> bool:
    if not task or task.type != "transfer" or task.stage in TERMINAL_STAGES:
        return False
    payload = task.payload if isinstance(task.payload, dict) else {}
    amount = payload.get("amount")
    return (
        bool(payload.get("source_account_id"))
        and not payload.get("funding_plan")
        and isinstance(amount, (int, float))
        and float(amount) > 0
    )


async def _maybe_coordinate_batch_funding(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    services: dict[str, Any],
    locale: str,
) -> dict[str, Any] | None:
    transfer_task_ids = [task_id for task_id in current_wave if _is_plannable_transfer_task(state.tasks.get(task_id))]
    if len(transfer_task_ids) < 2:
        return None

    transfer_worker = services.get("transfer")
    dd_provider = getattr(transfer_worker, "dd_provider", None) if transfer_worker else None
    if dd_provider is None:
        logger.info("batch_funding_coordinator_skipped", reason="dd_provider_missing", task_ids=transfer_task_ids)
        return None

    accounts_raw = (state.loaded_context or {}).get("accounts") or []
    transaction_accounts_raw = (state.loaded_context or {}).get("transaction_accounts") or accounts_raw
    accounts = [account for account in transaction_accounts_raw if isinstance(account, dict)]
    demands = [
        _build_transfer_demand(task_id, cast(dict[str, Any], state.tasks[task_id].payload))
        for task_id in transfer_task_ids
    ]
    coordinator = BatchFundingCoordinator(dd_provider=dd_provider)
    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale=locale)
    if result.is_feasible:
        for task_id in transfer_task_ids:
            plan = result.plans_by_task.get(task_id)
            if plan is None:
                continue
            payload = state.tasks[task_id].payload
            if not isinstance(payload, dict):
                continue
            payload["funding_plan"] = _funding_plan_to_payload_dict(plan, payload)
        logger.info(
            "batch_funding_coordinator_applied",
            task_count=len(result.plans_by_task),
            total_demanded=result.total_demanded,
            total_available=result.total_available,
        )
        return None

    prompt = result.suggestion or render_message("funding.batch.total_infeasible", locale)
    interrupt = PendingInterrupt(
        kind="input",
        task_ids=transfer_task_ids,
        fields_by_task={task_id: ["funding_plan"] for task_id in transfer_task_ids},
        prompt=prompt,
    )
    logger.info(
        "batch_funding_coordinator_blocked",
        task_count=len(transfer_task_ids),
        shortfall_count=len(result.shortfalls or []),
        total_demanded=result.total_demanded,
        total_available=result.total_available,
    )
    return {
        "pending_interrupt": interrupt,
        "tasks": state.tasks,
        "outbox": _with_policy_notice(state, [{"type": "say", "text": prompt}]),
        "policy_notice": None,
    }


async def advance_wave(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Execution Node.

    Iterates through tasks in current wave.
    Invokes Domain Workers.
    Aggregates outcomes and sets PendingInterrupt if blocked.
    """
    if not state.waves or state.current_wave_index >= len(state.waves):
        logger.info("advance_wave_skip", index=state.current_wave_index, count=len(state.waves))
        return {}

    current_wave = state.waves[state.current_wave_index]
    logger.info(
        "advance_wave", index=state.current_wave_index, tasks=current_wave, context_frames_len=len(state.context_frames)
    )

    # [SAFETY] If pending_interrupt is already set (e.g. valid restoration), do NOT execute tasks.
    # Return it to force graph to stop/route correctly.
    if state.pending_interrupt:
        logger.info("advance_wave_blocked_by_interrupt", kind=state.pending_interrupt.kind)
        return {"pending_interrupt": state.pending_interrupt}

    services = config["configurable"].get("services") or {}
    agg = ExecutionAggregation(state.tasks)

    ctx = ExecutionContext(
        state=state,
        config=config,
        services=services,
        current_wave_len=len(current_wave),
        current_wave_task_ids=list(current_wave),
        agg=agg,
    )
    locale = (state.loaded_context or {}).get("language", "en")
    mandate_gate_accounts: list[dict[str, Any]] = []

    # ── Build transaction-only account view without hiding pending accounts globally ──
    if state.loaded_context and "accounts" in state.loaded_context:
        raw_accounts = state.loaded_context["accounts"]
        mandate_gate_accounts = [a for a in raw_accounts if isinstance(a, dict)]
        logger.info(
            "mandate_gate_pre_filter",
            account_statuses=[
                {"bank": a.get("bank_name"), "mandate_status": a.get("mandate_status")}
                for a in raw_accounts
                if isinstance(a, dict)
            ],
        )
        state.loaded_context["transaction_accounts"] = [
            a for a in mandate_gate_accounts if a.get("mandate_status") == "ready"
        ]
        logger.info(
            "mandate_gate_post_filter",
            ready_count=len(state.loaded_context["transaction_accounts"]),
        )
    # ────────────────────────────────────────────────────────────────

    batch_block = await _maybe_coordinate_batch_funding(
        state=state,
        current_wave=current_wave,
        services=services,
        locale=locale,
    )
    if batch_block:
        return batch_block

    handlers = {
        "transfer": handle_transfer_task,
        "account": handle_account_task,
        "beneficiary": handle_beneficiary_task,
        "airtime": handle_airtime_task,
        "query": handle_query_task,
        "data": handle_data_task,
        "faq": handle_faq_task,
        "support": handle_support_task,
        "orchestrator": handle_orchestrator_task,
    }

    progressed = False
    for task_id in current_wave:
        task = state.tasks.get(task_id)
        if not task:
            continue

        if task.stage in TERMINAL_STAGES:
            continue

        if task.stage == TaskStage.AWAITING_CONFIRMATION:
            agg.needs_confirm_tasks.append(task_id)
            progressed = True
            continue

        if task.stage == TaskStage.AWAITING_AUTH:
            agg.needs_auth_tasks.append(task_id)
            progressed = True
            continue

        # During an input interrupt turn, only execute the task(s) currently collecting user input.
        # This prevents non-focused tasks in the same wave from drifting state and cross-contaminating
        # multi-transfer slot filling.
        if (
            state.last_interrupt
            and state.last_interrupt.kind == "input"
            and state.last_interrupt.task_ids
            and task_id not in state.last_interrupt.task_ids
            and task.stage in INPUT_MUTABLE_STAGES
        ):
            logger.info(
                "task_deferred_during_input_interrupt",
                task_id=task_id,
                active_task_ids=state.last_interrupt.task_ids,
            )
            continue

        dep_status, dep_id = _dependency_resolution(task, state.tasks)
        if dep_status == "cancel":
            task.stage = TaskStage.CANCELLED
            task.payload["error"] = f"dependency {dep_id} not successful"
            logger.info("task_cancelled_by_dependency", task_id=task_id, dependency=dep_id)
            progressed = True
            continue
        if dep_status == "wait":
            logger.info("task_waiting_for_dependency", task_id=task_id, dependency=dep_id)
            continue

        handler = handlers.get(task.type)
        if not handler:
            continue

        if task.type in TRANSACTION_TASK_TYPES:
            accounts = (state.loaded_context or {}).get("transaction_accounts") or []
            has_ready = any(isinstance(a, dict) and a.get("mandate_status") == "ready" for a in accounts)
            if not has_ready:
                mandate_error = _build_mandate_gate_error(mandate_gate_accounts or accounts, locale)

                task.stage = TaskStage.FAILED
                task.payload["is_pending_mandate"] = True
                task.payload["mandate_accounts"] = mandate_gate_accounts
                task.payload["error"] = mandate_error
                progressed = True
                continue

        await handler(task, task_id, ctx)
        progressed = True

    if not progressed:
        pending = []
        for task_id in current_wave:
            task = state.tasks.get(task_id)
            if task and task.stage not in TERMINAL_STAGES:
                pending.append(task_id)
        if pending:
            logger.warning("dependency_deadlock_wave_cancelled", wave=current_wave, pending_tasks=pending)
            for task_id in pending:
                task = state.tasks[task_id]
                task.stage = TaskStage.CANCELLED
                task.payload["error"] = "unresolved dependency deadlock"

    if agg.missing_fields_by_task:
        beneficiary_blockers = [
            tid for tid, fields in agg.missing_fields_by_task.items() if "beneficiary_id" in set(fields)
        ]
        if beneficiary_blockers:
            focused_beneficiary_tid = next(
                (tid for tid in current_wave if tid in beneficiary_blockers), beneficiary_blockers[0]
            )
            for tid in list(agg.missing_fields_by_task):
                if tid != focused_beneficiary_tid:
                    agg.missing_fields_by_task.pop(tid, None)
                    agg.prompts_by_task.pop(tid, None)
                    agg.details_by_task.pop(tid, None)
            agg.missing_fields_by_task[focused_beneficiary_tid] = ["beneficiary_id"]
            logger.info(
                "beneficiary_ambiguity_blocking_mode",
                focused_task_id=focused_beneficiary_tid,
                suppressed_count=max(len(beneficiary_blockers) - 1, 0),
            )

        # [Prioritized Prompting]
        # If ANY task needs basic details (beneficiary, amount, etc.), suppress "Execution" prompts (Source/PIN)
        # for ALL tasks. This prevents confusing parallel prompts like "Select Account" + "Who is Dad?".

        has_basic_blocker = False
        for fields in agg.missing_fields_by_task.values():
            if any(f not in EXECUTION_ONLY_FIELDS for f in fields):
                has_basic_blocker = True
                break

        if has_basic_blocker:
            # Suppress tasks that are ONLY waiting for execution fields
            suppressed_tasks = []
            for tid, fields in agg.missing_fields_by_task.items():
                if all(f in EXECUTION_ONLY_FIELDS for f in fields):
                    suppressed_tasks.append(tid)

            for tid in suppressed_tasks:
                del agg.missing_fields_by_task[tid]
                logger.info("suppressed_execution_prompt", task_id=tid, reason="basic_blocker_active")

        # [Batch transfer] If ALL blocked tasks are transfer and ONLY need source_account_id,
        # ask once for the whole batch (one "Which account?" prompt) instead of per-task.
        transfer_tasks_only_source = [
            tid
            for tid in agg.missing_fields_by_task
            if state.tasks.get(tid)
            and state.tasks[tid].type == "transfer"
            and set(agg.missing_fields_by_task[tid]) == {"source_account_id"}
        ]
        all_batch_source = len(transfer_tasks_only_source) >= 2 and len(transfer_tasks_only_source) == len(
            agg.missing_fields_by_task
        )

        if all_batch_source:
            # [Option C] Batch funding: only newly resolved in prompt, then amount + account list.
            accounts = state.loaded_context.get("transaction_accounts") or []
            lines = []
            total_amount = 0.0
            amounts = []
            for tid in transfer_tasks_only_source:
                task = state.tasks[tid]
                amt = task.payload.get("amount") or 0.0
                total_amount += amt
                amounts.append(amt)
                if task.payload.get("recipient_ui_confirmed"):
                    continue
                label = (
                    task.payload.get("recipient_name")
                    or task.payload.get("recipient_resolved_name")
                    or render_message("orchestrator.finalize.recipient_fallback", locale)
                )
                resolved = task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name") or ""
                bank = task.payload.get("recipient_bank_name")
                acc = task.payload.get("recipient_account")
                if bank and acc and resolved:
                    lines.append(
                        render_message(
                            "orchestrator.execution.recipient_resolved_with_bank",
                            locale,
                            {"label": label, "resolved": resolved, "bank": bank, "account": acc},
                        )
                    )
                elif resolved:
                    lines.append(
                        render_message(
                            "orchestrator.execution.recipient_resolved",
                            locale,
                            {"label": label, "resolved": resolved},
                        )
                    )
                else:
                    lines.append(
                        render_message(
                            "orchestrator.execution.recipient_resolved_generic",
                            locale,
                            {"label": label},
                        )
                    )

            prompt_text = format_batch_transfer_source_prompt(
                resolved_lines=lines,
                total_amount=total_amount,
                amounts=amounts,
                accounts=accounts,
                locale=locale,
            )
            for tid in transfer_tasks_only_source:
                state.tasks[tid].payload["recipient_ui_confirmed"] = True
        else:
            # [One-at-a-time] When multiple tasks need basic details, focus on one per turn.
            tasks_needing_basic = [
                tid
                for tid in agg.missing_fields_by_task
                if any(f not in EXECUTION_ONLY_FIELDS for f in agg.missing_fields_by_task[tid])
            ]
            focused_tid = None
            if tasks_needing_basic:
                for tid in current_wave:
                    if tid in tasks_needing_basic:
                        focused_tid = tid
                        break

            if focused_tid is not None:
                # One-at-a-time: prompt for one recipient only; only that task gets next message.
                focused_task = state.tasks[focused_tid]
                focused_name = (
                    focused_task.payload.get("recipient_name")
                    or focused_task.payload.get("recipient_resolved_name")
                    or "this recipient"
                )
                just_resolved_tid = None
                just_resolved_name = None
                just_resolved_bank = None
                if state.last_interrupt and state.last_interrupt.task_ids:
                    for tid in state.last_interrupt.task_ids:
                        if tid not in agg.missing_fields_by_task and state.tasks.get(tid):
                            just_resolved_tid = tid
                            rt = state.tasks[just_resolved_tid]
                            just_resolved_name = _recipient_prompt_label(cast(dict[str, Any], rt.payload))
                            just_resolved_bank = rt.payload.get("recipient_bank_name")
                            break

                found_names = []
                if just_resolved_tid is None:
                    for tid in current_wave:
                        task = state.tasks.get(tid)
                        if not task or task.stage in TERMINAL_STAGES:
                            continue
                        if tid not in agg.missing_fields_by_task:
                            if name := _recipient_prompt_label(cast(dict[str, Any], task.payload)):
                                if name not in found_names:
                                    found_names.append(name)

                focused_missing_fields = agg.missing_fields_by_task[focused_tid]
                focused_worker_prompt = agg.prompts_by_task.get(focused_tid)
                focused_details = agg.details_by_task.get(focused_tid)
                has_structured_options = isinstance(focused_details, dict) and isinstance(
                    focused_details.get("options"), list
                )
                transfer_recipient_fields = {"recipient_account", "recipient_bank_name"}
                is_transfer_recipient_prompt = bool(set(focused_missing_fields) & transfer_recipient_fields)
                if focused_worker_prompt and ("beneficiary_id" in focused_missing_fields or has_structured_options):
                    prompt_text = _compact_prompt_for_options(focused_worker_prompt)
                elif focused_worker_prompt and not is_transfer_recipient_prompt:
                    # Non-transfer fields (e.g. airtime recipient_phone, amount): use the worker's own prompt.
                    prompt_text = focused_worker_prompt
                else:
                    prompt_text = format_single_transfer_recipient_prompt(
                        focused_name=focused_name,
                        focused_missing_fields=focused_missing_fields,
                        just_resolved_name=just_resolved_name,
                        just_resolved_bank=just_resolved_bank,
                        found_names=found_names,
                        locale=locale,
                    )
                queued_tasks = _queued_transaction_tasks_for_focus(
                    state=state,
                    current_wave=current_wave,
                    focused_tid=focused_tid,
                )
                prompt_text, queue_meta = _append_queued_notice(
                    prompt_text=prompt_text,
                    queued_tasks=queued_tasks,
                    locale=locale,
                )

                # Mark recipients we mentioned as announced (found_names + just_resolved)
                for tid in current_wave:
                    task = state.tasks.get(tid)
                    if not task:
                        continue
                    if tid == focused_tid:
                        continue
                    name = task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name")
                    if name and (tid not in agg.missing_fields_by_task or tid == just_resolved_tid):
                        task.payload["recipient_ui_confirmed"] = True
                interrupt = PendingInterrupt(
                    kind="input",
                    task_ids=[focused_tid],
                    fields_by_task={focused_tid: agg.missing_fields_by_task[focused_tid]},
                    prompt=prompt_text,
                )
                details = focused_details
                options_entry = _build_show_options_entry(
                    details=details,
                    prompt_text=prompt_text,
                    task_id=focused_tid,
                    focused_missing_fields=focused_missing_fields,
                )
                outbox_entries = [options_entry] if options_entry else [{"type": "say", "text": prompt_text}]
                if queue_meta is not None:
                    outbox_entries[0]["queue"] = queue_meta
                return {
                    "pending_interrupt": interrupt,
                    "tasks": state.tasks,
                    "outbox": _with_policy_notice(state, outbox_entries),
                    "policy_notice": None,
                }

            # [UX] Smart Unified Prompt (Found X, Missing Y) — multiple blockers or no single focus
            found_names = []
            missing_prompts = []

            for tid in current_wave:
                task = state.tasks.get(tid)
                if not task or task.stage in TERMINAL_STAGES:
                    continue

                # Collect names for "I found X" (only if hasn't been announced to UI yet)
                if not task.payload.get("recipient_ui_confirmed"):
                    name = _recipient_prompt_label(cast(dict[str, Any], task.payload))
                    if name and name not in found_names:
                        found_names.append(name)

                # Collect missing prompts
                if tid in agg.missing_fields_by_task:
                    if p := agg.prompts_by_task.get(tid):
                        missing_prompts.append(p)

            # [Source Repair Flow]
            # If tasks share a failed source bank hint, use the detailed repair UI
            repair_hint = None
            if agg.source_bank_hints:
                # Use the most common hint (or first one)
                repair_hint = agg.source_bank_hints[0]

            if repair_hint and agg.feedback_messages:
                intents = []
                for tid in current_wave:
                    task = state.tasks.get(tid)
                    if not task or task.stage in TERMINAL_STAGES:
                        continue
                    intents.append(format_intent_line(task.type, task.payload, locale=locale))

                accounts = state.loaded_context.get("transaction_accounts", [])
                prompt_text = format_source_repair_prompt(
                    intents=intents,
                    failed_hint=repair_hint,
                    accounts=accounts,
                    locale=locale,
                )
            else:
                prompt_text = format_missing_details_prompt(
                    found_names=found_names,
                    missing_prompts=missing_prompts,
                    feedback_messages=agg.feedback_messages,
                    locale=locale,
                )

            # Mark resolved tasks that were mentioned in the prompt
            for tid in current_wave:
                task = state.tasks.get(tid)
                if not task:
                    continue
                name = _recipient_prompt_label(cast(dict[str, Any], task.payload))
                if name and name in found_names:
                    task.payload["recipient_ui_confirmed"] = True

        fallback_options_entry: dict[str, Any] | None = None
        if len(agg.missing_fields_by_task) == 1:
            task_id = next(iter(agg.missing_fields_by_task.keys()))
            details = agg.details_by_task.get(task_id)
            focused_missing_fields = agg.missing_fields_by_task.get(task_id, [])
            fallback_options_entry = _build_show_options_entry(
                details=details,
                prompt_text=prompt_text,
                task_id=task_id,
                focused_missing_fields=focused_missing_fields,
            )
            if fallback_options_entry:
                prompt_text = _compact_prompt_for_options(prompt_text)
                fallback_options_entry["title"] = prompt_text

        interrupt = PendingInterrupt(
            kind="input",
            task_ids=list(agg.missing_fields_by_task.keys()),
            fields_by_task=agg.missing_fields_by_task,
            prompt=prompt_text,
        )
        fallback_outbox_entries: list[dict[str, Any]] = [{"type": "say", "text": prompt_text}]
        if fallback_options_entry:
            fallback_outbox_entries = [fallback_options_entry]
        return {
            "pending_interrupt": interrupt,
            "tasks": state.tasks,
            "outbox": _with_policy_notice(state, fallback_outbox_entries),
            "policy_notice": None,
        }

    updates = agg.updates
    if state.policy_notice:
        existing = updates.get("outbox", [])
        updates["outbox"] = _with_policy_notice(state, existing)
        updates["policy_notice"] = None

    # [Confirmation Aggregation]
    if agg.needs_confirm_tasks:
        confirm_task_ids = _dedupe_task_ids(
            [task_id for task_id in agg.needs_confirm_tasks if task_id in state.tasks],
            current_wave,
        )
        if not confirm_task_ids:
            return cast(dict[str, Any], updates)
        accounts_raw = state.loaded_context.get("accounts") or []
        accounts = [account for account in accounts_raw if isinstance(account, dict)]
        summ = _build_confirmation_gate_summary(
            state=state,
            task_ids=confirm_task_ids,
            locale=locale,
            accounts=accounts,
        )

        first_task_payload = state.tasks[confirm_task_ids[0]].payload.get("confirmation", {})
        snap = first_task_payload.get("snapshot", {})
        update_messages: list[str] = []
        for task_id in confirm_task_ids:
            confirmation_payload = state.tasks[task_id].payload.get("confirmation", {})
            candidate = confirmation_payload.get("update_message")
            if isinstance(candidate, str) and candidate.strip():
                update_messages.append(candidate)
        update_msg = _compact_confirmation_update_message(update_messages, locale)
        snapshots_by_task = {
            tid: state.tasks[tid].payload.get("confirmation", {}).get("snapshot", {}) for tid in confirm_task_ids
        }

        interrupt = PendingInterrupt(
            kind="confirmation",
            task_ids=confirm_task_ids,
            prompt=summ,
        )

        outbox = []
        if update_msg:
            outbox.append({"type": "say", "text": update_msg})

        outbox.append(
            {
                "type": "request_confirmation",
                "task_ids": confirm_task_ids,
                "header": build_confirmation_header(
                    task_types=[state.tasks[task_id].type for task_id in confirm_task_ids if task_id in state.tasks],
                    locale=locale,
                    task_count=len(confirm_task_ids),
                ),
                "summary": summ,
                "snapshot": snap,
                "snapshots_by_task": snapshots_by_task,
                "idempotency_key": state.tasks[confirm_task_ids[0]].payload.get(
                    "idempotency_key",
                    "unknown",
                ),
                "actionable_payload": build_actionable_payload(state.tasks[confirm_task_ids[0]]),
            }
        )

        updates["outbox"] = outbox
        updates["pending_interrupt"] = interrupt
        return cast(dict[str, Any], updates)

    if agg.needs_auth_tasks:
        auth_task_ids = _dedupe_task_ids(
            [task_id for task_id in agg.needs_auth_tasks if task_id in state.tasks],
            current_wave,
        )
        if not auth_task_ids:
            return cast(dict[str, Any], updates)

        first_task = state.tasks[auth_task_ids[0]]
        accounts_raw = state.loaded_context.get("accounts") or []
        accounts = [account for account in accounts_raw if isinstance(account, dict)]
        summ = _build_confirmation_gate_summary(
            state=state,
            task_ids=auth_task_ids,
            locale=locale,
            accounts=accounts,
        )
        if not summ:
            summ = render_message("orchestrator.execution.pin_prompt_default", locale)
        snap = first_task.payload.get("confirmation", {}).get("snapshot", {})
        snapshots_by_task = {
            tid: state.tasks[tid].payload.get("confirmation", {}).get("snapshot", {}) for tid in auth_task_ids
        }

        idem_key = first_task.payload.get("idempotency_key", "no-key")

        reason = _auth_header_for_tasks(state, auth_task_ids, locale=locale)

        interrupt = PendingInterrupt(kind="auth", task_ids=auth_task_ids, auth_method="pin", prompt=summ)
        updates["outbox"] = [
            {
                "type": "auth_request",
                "method": "pin",
                "task_ids": auth_task_ids,
                "idempotency_key": idem_key,
                "header": reason,
                "summary": summ,
                "snapshot": snap,
                "snapshots_by_task": snapshots_by_task,
                "actionable_payload": build_actionable_payload(first_task),
            }
        ]
        updates["pending_interrupt"] = interrupt
        return cast(dict[str, Any], updates)

    all_terminal = True
    for task_id in current_wave:
        task = state.tasks.get(task_id)
        if not task:
            continue
        if task.stage not in TERMINAL_STAGES:
            all_terminal = False
            break

    if all_terminal and "current_wave_index" not in updates:
        updates["current_wave_index"] = state.current_wave_index + 1

    return cast(dict[str, Any], updates)
