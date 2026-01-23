import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import (
    AccountOutcome,
    PendingInterrupt,
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
            transfer_service = services.get("transfer")
            if not transfer_service or not hasattr(transfer_service, "worker"):
                logger.error("transfer_worker_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: Transfer worker unavailable"
                continue

            worker = transfer_service.worker

            user_msg = None
            if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
                user_msg = state.last_message_text

            user_repo = config["configurable"].get("user_repo")
            account_repo = config["configurable"].get("account_repo")
            beneficiary_repo = config["configurable"].get("beneficiary_repo")

            context_data = {"phone_number": state.phone_number}

            try:
                if user_repo:
                    user = await asyncio.to_thread(user_repo.get_by_phone, state.phone_number)
                    if user:
                        context_data["user_id"] = user.id

                        tasks = []
                        if beneficiary_repo:
                            tasks.append(asyncio.to_thread(beneficiary_repo.get_by_user, user.id))
                        else:
                            tasks.append(asyncio.sleep(0))

                        if account_repo:
                            tasks.append(asyncio.to_thread(account_repo.get_by_user, user.id))
                        else:
                            tasks.append(asyncio.sleep(0))

                        results = await asyncio.gather(*tasks)

                        def to_dict(obj):
                            """Convert SQLAlchemy model to dict, filtering internal state."""
                            if not obj:
                                return {}
                            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}

                        if beneficiary_repo:
                            context_data["beneficiaries"] = [to_dict(b) for b in results[0]]
                        if account_repo:
                            context_data["accounts"] = [to_dict(a) for a in results[1]]
            except Exception as e:
                logger.error("context_loading_failed", error=str(e))

            pass

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
            account_service = services.get("account")
            if not account_service or not hasattr(account_service, "worker"):
                logger.error("account_worker_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: Account worker unavailable"
                continue

            worker = account_service.worker

            user_msg = None
            if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
                user_msg = state.last_message_text

            user_repo = config["configurable"].get("user_repo")
            account_repo = config["configurable"].get("account_repo")
            redis_client = config["configurable"].get("redis_client")

            context_data = {"phone_number": state.phone_number}

            try:
                if user_repo:
                    user = await asyncio.to_thread(user_repo.get_by_phone, state.phone_number)
                    if user:
                        context_data["user_id"] = user.id

                        def to_dict(obj):
                            """Convert SQLAlchemy model to dict, filtering internal state."""
                            if not obj:
                                return {}
                            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}

                        context_data["profile"] = to_dict(user)

                        if account_repo:
                            accounts = await asyncio.to_thread(account_repo.get_by_user, user.id)
                            context_data["accounts"] = [to_dict(a) for a in accounts]

                if redis_client:
                    language = await redis_client.get(f"user:{state.phone_number}:language")
                    if isinstance(language, bytes):
                        language = language.decode()
                    if language:
                        context_data["language"] = language
            except Exception as e:
                logger.error("account_context_failed", error=str(e))

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
            airtime_service = services.get("airtime")
            if not airtime_service or not hasattr(airtime_service, "worker"):
                logger.error("airtime_worker_missing")
                task.stage = TaskStage.FAILED
                task.payload["error"] = "System error: Airtime worker unavailable"
                continue

            worker = airtime_service.worker

            user_msg = None
            if task.stage in (TaskStage.DRAFT, TaskStage.EXTRACTED):
                user_msg = state.last_message_text

            user_repo = config["configurable"].get("user_repo")
            account_repo = config["configurable"].get("account_repo")
            beneficiary_repo = config["configurable"].get("beneficiary_repo")

            context_data = {"phone_number": state.phone_number}

            try:
                if user_repo:
                    user = await asyncio.to_thread(user_repo.get_by_phone, state.phone_number)
                    if user:
                        context_data["user_id"] = user.id

                        # Load accounts and beneficiaries
                        tasks = []
                        if beneficiary_repo:
                            tasks.append(asyncio.to_thread(beneficiary_repo.get_by_user, user.id))
                        else:
                            tasks.append(asyncio.sleep(0))

                        if account_repo:
                            tasks.append(asyncio.to_thread(account_repo.get_by_user, user.id))
                        else:
                            tasks.append(asyncio.sleep(0))
                        
                        results = await asyncio.gather(*tasks)

                        def to_dict(obj):
                            if not obj: return {}
                            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
                        
                        if beneficiary_repo:
                             context_data["beneficiaries"] = [to_dict(b) for b in results[0]]
                        if account_repo:
                             context_data["accounts"] = [to_dict(a) for a in results[1]]
            except Exception as e:
                logger.error("airtime_context_failed", error=str(e))

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
