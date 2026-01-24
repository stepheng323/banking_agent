from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import (
    AccountOutcome,
    FAQOutcome,
    PendingInterrupt,
    SupportOutcome,
    TaskStage,
    TransactionOutcome,
)
from apps.core.src.agent.orchestrator.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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
    logger.info("advance_wave", index=state.current_wave_index, tasks=current_wave)

    services = config["configurable"].get("services")

    updates = {"tasks": state.tasks}

    missing_fields_by_task = {}
    needs_confirm_tasks = []
    needs_auth_tasks = []
    prompts = []

    for tid in current_wave:
        task = state.tasks.get(tid)
        if not task:
            continue

        if task.stage in (TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED):
            continue

        if task.type == "transfer":
            worker = services.get("transfer")
            if not worker:
                logger.error("transfer_worker_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: Transfer worker unavailable"
                continue

            user_msg = None
            if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
                user_msg = state.last_message_text

            context_data = {
                "phone_number": state.phone_number,
                "user_id": state.loaded_context.get("user_id"),
                "accounts": state.loaded_context.get("accounts", []),
                "beneficiaries": state.loaded_context.get("beneficiaries", []),
            }

            result = await worker.run(
                payload=task.payload,
                context=context_data,
                user_message=user_msg,
                pin_verified=state.pin_verified,
            )

            if result.patch:
                task.payload.update(result.patch)

            if result.outcome == TransactionOutcome.OK:
                if result.receipt:
                    task.stage = TaskStage.COMPLETED
                    task.payload["receipt"] = result.receipt
                else:
                    pass

            elif result.outcome == TransactionOutcome.NEEDS_INPUT:
                task.stage = TaskStage.EXTRACTED
                if result.required_fields:
                    missing_fields_by_task[tid] = result.required_fields
                if result.prompt:
                    prompts.append(result.prompt)

            elif result.outcome == TransactionOutcome.NEEDS_CONFIRMATION:
                task.stage = TaskStage.AWAITING_CONFIRMATION
                needs_confirm_tasks.append(tid)
                if result.confirmation_snapshot:
                    if "confirmation" not in task.payload:
                        task.payload["confirmation"] = {}
                    task.payload["confirmation"]["summary"] = result.confirmation_summary
                    task.payload["confirmation"]["snapshot"] = result.confirmation_snapshot
                    if getattr(result, "update_message", None):
                        task.payload["confirmation"]["update_message"] = result.update_message

            elif result.outcome == TransactionOutcome.NEEDS_AUTH:
                task.stage = TaskStage.AWAITING_AUTH
                needs_auth_tasks.append(tid)

            elif result.outcome == TransactionOutcome.FAILED:
                task.stage = TaskStage.FAILED
                task.payload["error"] = result.error

        elif task.type == "account":
            worker = services.get("account")
            if not worker:
                logger.error("account_worker_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: Account worker unavailable"
                continue

            user_msg = None
            if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
                user_msg = state.last_message_text

            context_data = {
                "phone_number": state.phone_number,
                "user_id": state.loaded_context.get("user_id"),
                "profile": state.loaded_context.get("profile", {}),
                "accounts": state.loaded_context.get("accounts", []),
                "language": state.loaded_context.get("language"),
            }

            result = await worker.run(
                payload=task.payload,
                context=context_data,
                user_message=user_msg,
            )

            if result.patch:
                task.payload.update(result.patch)

            if result.outcome == AccountOutcome.OK:
                task.stage = TaskStage.COMPLETED
                if result.response:
                    task.payload["result"] = result.response
                    updates.setdefault("outbox", [])
                    updates["outbox"].append({"type": "say", "text": result.response})
                if result.outbox:
                    updates.setdefault("outbox", [])
                    updates["outbox"].extend(result.outbox)

            elif result.outcome == AccountOutcome.NEEDS_INPUT:
                task.stage = TaskStage.EXTRACTED
                missing_fields_by_task[tid] = result.required_fields or ["identifier"]
                if result.prompt:
                    prompts.append(result.prompt)

            elif result.outcome == AccountOutcome.FAILED:
                task.stage = TaskStage.FAILED
                task.payload["error"] = result.error or "Account action failed."
                if result.response:
                    updates.setdefault("outbox", [])
                    updates["outbox"].append({"type": "say", "text": result.response})

        elif task.type == "beneficiary":
            suggestion_service = config["configurable"].get("beneficiary_suggestion_service")
            if not suggestion_service:
                logger.error("suggestion_service_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: Suggestion service unavailable"
                continue

            alias = task.payload.get("alias")
            try:
                msg = await suggestion_service.save_beneficiary(state.phone_number, alias=alias)
                task.stage = TaskStage.COMPLETED
                task.payload["result"] = msg

                if len(current_wave) == 1:
                    updates.setdefault("outbox", [])
                    updates["outbox"].append({"type": "say", "text": msg})

            except Exception as e:
                logger.error("save_beneficiary_exec_error", error=str(e))
                task.stage = TaskStage.FAILED
                task.payload["error"] = "Failed to save beneficiary."

                task.stage = TaskStage.FAILED
                task.payload["error"] = "Failed to save beneficiary."

        elif task.type == "airtime":
            worker = services.get("airtime")
            if not worker:
                logger.error("airtime_worker_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: Airtime worker unavailable"
                continue

            user_msg = None
            if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
                user_msg = state.last_message_text

            context_data = {
                "phone_number": state.phone_number,
                "user_id": state.loaded_context.get("user_id"),
                "accounts": state.loaded_context.get("accounts", []),
                "beneficiaries": state.loaded_context.get("beneficiaries", []),
            }

            result = await worker.run(
                payload=task.payload,
                context=context_data,
                user_message=user_msg,
                pin_verified=state.pin_verified,
            )

            if result.patch:
                task.payload.update(result.patch)

            if result.outcome == TransactionOutcome.OK:
                if result.receipt:
                    task.stage = TaskStage.COMPLETED
                    task.payload["receipt"] = result.receipt

            elif result.outcome == TransactionOutcome.NEEDS_INPUT:
                task.stage = TaskStage.EXTRACTED
                if result.required_fields:
                    missing_fields_by_task[tid] = result.required_fields
                if result.prompt:
                    prompts.append(result.prompt)

            elif result.outcome == TransactionOutcome.NEEDS_CONFIRMATION:
                task.stage = TaskStage.AWAITING_CONFIRMATION
                needs_confirm_tasks.append(tid)
                if result.confirmation_summary:
                    if "confirmation" not in task.payload:
                        task.payload["confirmation"] = {}
                    task.payload["confirmation"]["summary"] = result.confirmation_summary
                    task.payload["confirmation"]["snapshot"] = result.confirmation_snapshot
                    if getattr(result, "update_message", None):
                        task.payload["confirmation"]["update_message"] = result.update_message

            elif result.outcome == TransactionOutcome.NEEDS_AUTH:
                task.stage = TaskStage.AWAITING_AUTH
                needs_auth_tasks.append(tid)

            elif result.outcome == TransactionOutcome.FAILED:
                task.stage = TaskStage.FAILED
                task.payload["error"] = result.error or "Airtime purchase failed"

        elif task.type == "query":
            worker = services.get("query")
            if not worker:
                logger.error("query_worker_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: Query worker unavailable"
                continue

            user_msg = state.last_message_text

            context_data = {
                "phone_number": state.phone_number,
                "user_id": state.loaded_context.get("user_id"),
                "profile": state.loaded_context.get("profile", {}),
                "accounts": state.loaded_context.get("accounts", []),
            }

            result = await worker.run(
                payload=task.payload,
                context=context_data,
            )

            if result.patch:
                task.payload.update(result.patch)

            if result.outcome == TransactionOutcome.OK:
                task.stage = TaskStage.COMPLETED
                if result.response:
                    task.payload["result"] = result.response
                    updates.setdefault("outbox", [])
                    updates["outbox"].append({"type": "say", "text": result.response})

            elif result.outcome == TransactionOutcome.NEEDS_INPUT:
                task.stage = TaskStage.EXTRACTED
                # If response is present, treat as a prompt
                if result.response:
                    prompts.append(result.response)
                    # For query flow, "missing fields" is generic, maybe just use "query_clarification"
                    missing_fields_by_task[tid] = ["clarification"]

            elif result.outcome == TransactionOutcome.FAILED:
                task.stage = TaskStage.FAILED
                task.payload["error"] = result.error or "Query processing failed."
                if result.response:  # Sometimes failed items have a polite response
                    updates.setdefault("outbox", [])
                    updates["outbox"].append({"type": "say", "text": result.response})

        elif task.type == "data":
            worker = services.get("data")
            if not worker:
                logger.error("data_worker_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: Data worker unavailable"
                continue

            user_msg = None
            if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
                user_msg = state.last_message_text

            context_data = {
                "phone_number": state.phone_number,
                "user_id": state.loaded_context.get("user_id"),
                "accounts": state.loaded_context.get("accounts", []),
                "beneficiaries": state.loaded_context.get("beneficiaries", []),
            }

            result = await worker.run(
                payload=task.payload,
                context=context_data,
                user_message=user_msg,
                pin_verified=state.pin_verified,
            )

            if result.patch:
                task.payload.update(result.patch)

            if result.outcome == TransactionOutcome.OK:
                if result.receipt:
                    task.stage = TaskStage.COMPLETED
                    task.payload["receipt"] = result.receipt

            elif result.outcome == TransactionOutcome.NEEDS_INPUT:
                task.stage = TaskStage.EXTRACTED
                if result.required_fields:
                    missing_fields_by_task[tid] = result.required_fields
                if result.prompt:
                    prompts.append(result.prompt)

            elif result.outcome == TransactionOutcome.NEEDS_CONFIRMATION:
                task.stage = TaskStage.AWAITING_CONFIRMATION
                needs_confirm_tasks.append(tid)
                if result.confirmation_summary:
                    if "confirmation" not in task.payload:
                        task.payload["confirmation"] = {}
                    task.payload["confirmation"]["summary"] = result.confirmation_summary
                    task.payload["confirmation"]["snapshot"] = result.confirmation_snapshot
                    if getattr(result, "update_message", None):
                        task.payload["confirmation"]["update_message"] = result.update_message

            elif result.outcome == TransactionOutcome.NEEDS_AUTH:
                task.stage = TaskStage.AWAITING_AUTH
                needs_auth_tasks.append(tid)

            elif result.outcome == TransactionOutcome.FAILED:
                task.stage = TaskStage.FAILED
                task.payload["error"] = result.error or "Data purchase failed"

        elif task.type == "faq":
            worker = services.get("faq")
            if not worker:
                logger.error("faq_worker_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: FAQ worker unavailable"
                continue

            user_msg = state.last_message_text
            context_data = {"phone_number": state.phone_number}

            result = await worker.run(
                payload=task.payload,
                context=context_data,
                user_message=user_msg,
            )

            if result.outcome == FAQOutcome.OK:
                task.stage = TaskStage.COMPLETED
                if result.response:
                    updates.setdefault("outbox", [])
                    updates["outbox"].append({"type": "say", "text": result.response})
            elif result.outcome == FAQOutcome.FAILED:
                task.stage = TaskStage.FAILED
                task.payload["error"] = result.error or "FAQ failed"
                updates.setdefault("outbox", [])
                updates["outbox"].append({"type": "say", "text": "I'm having trouble retrieving that information."})

        elif task.type == "support":
            worker = services.get("support")
            if not worker:
                logger.error("support_worker_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: Support worker unavailable"
                continue

            user_msg = state.last_message_text
            context_data = {"phone_number": state.phone_number}

            context_data = {
                "phone_number": state.phone_number,
                "user_id": state.loaded_context.get("user_id"),
                "email": state.loaded_context.get("profile", {}).get("email"),
            }

            result = await worker.run(
                payload=task.payload,
                context=context_data,
                user_message=user_msg,
            )

            if result.outcome == SupportOutcome.OK:
                task.stage = TaskStage.COMPLETED
                if result.response:
                    updates.setdefault("outbox", [])
                    updates["outbox"].append({"type": "say", "text": result.response})
            elif result.outcome == SupportOutcome.NEEDS_INPUT:
                # Support might need clarification
                task.stage = TaskStage.EXTRACTED
                if result.response:
                    prompts.append(result.response)
                    missing_fields_by_task[tid] = ["clarification"]
            elif result.outcome == SupportOutcome.FAILED:
                task.stage = TaskStage.FAILED
                task.payload["error"] = result.error or "Support flow failed"
                updates.setdefault("outbox", [])
                updates["outbox"].append({"type": "say", "text": "I can't access support right now."})

    if missing_fields_by_task:
        prompt_text = "\n".join(prompts) or "I need some details."
        interrupt = PendingInterrupt(
            kind="input",
            task_ids=list(missing_fields_by_task.keys()),
            fields_by_task=missing_fields_by_task,
            prompt=prompt_text,
        )
        return {
            "pending_interrupt": interrupt,
            "tasks": state.tasks,
            "outbox": [{"type": "say", "text": prompt_text}],
        }

    if needs_confirm_tasks:
        confirmation_payload = state.tasks[needs_confirm_tasks[0]].payload["confirmation"]
        summ = confirmation_payload.get("summary", "Confirm transaction?")
        snap = confirmation_payload.get("snapshot", {})
        update_msg = confirmation_payload.get("update_message")

        interrupt = PendingInterrupt(
            kind="confirmation",
            task_ids=needs_confirm_tasks,
        )

        outbox = []
        if update_msg:
            outbox.append(
                {
                    "type": "say",
                    "text": update_msg,
                }
            )

        outbox.append(
            {
                "type": "request_confirmation",
                "task_ids": needs_confirm_tasks,
                "summary": summ,
                "snapshot": snap,
                "idempotency_key": state.tasks[needs_confirm_tasks[0]].payload.get("idempotency_key", "unknown"),
            }
        )

        updates["outbox"] = outbox
        updates["pending_interrupt"] = interrupt
        return updates

    if needs_auth_tasks:
        first_task = state.tasks[needs_auth_tasks[0]]
        summ = first_task.payload.get("confirmation", {}).get("summary", "Please enter your PIN.")
        snap = first_task.payload.get("confirmation", {}).get("snapshot", {})

        idem_key = first_task.payload.get("idempotency_key", "no-key")

        task_type = first_task.type
        reason = "Authorize Transaction"
        if task_type == "transfer":
            reason = "Transfer Authorization"
        elif task_type == "airtime":
            reason = "Airtime Purchase"
        elif task_type == "data":
            reason = "Data Purchase"

        interrupt = PendingInterrupt(kind="auth", task_ids=needs_auth_tasks, auth_method="pin", prompt=summ)
        updates["outbox"] = [
            {
                "type": "auth_request",
                "method": "pin",
                "task_ids": needs_auth_tasks,
                "idempotency_key": idem_key,
                "header": reason,
                "summary": summ,
            }
        ]
        updates["pending_interrupt"] = interrupt
        return updates

    all_terminal = all(
        state.tasks[tid].stage in (TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED) for tid in current_wave
    )

    if all_terminal:
        updates["current_wave_index"] = state.current_wave_index + 1

    return updates
