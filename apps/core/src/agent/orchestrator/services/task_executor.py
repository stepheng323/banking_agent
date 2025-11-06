"""Task execution service that routes to specialized agents."""

from typing import Any, Dict, List, Optional, Tuple

from apps.core.src.agent.conversation_context import ConversationContext
from apps.core.src.agent.banking.tools.account_tools import get_user_accounts
from shared.types.agent_types import TaskStatus


class TaskExecutor:
    """Executes planned tasks by routing to specialized agents."""

    def __init__(self, agent_invoker):
        """Initialize with agent invoker service."""
        self.agent_invoker = agent_invoker

    def _should_run_conditional_task(
        self, condition: Optional[str], prior_results: List[Dict[str, Any]]
    ) -> bool:
        """Evaluate whether a conditional task should execute."""
        if not condition:
            return True

        normalized_condition = condition.lower()
        if "insufficient" in normalized_condition:
            return any(
                result.get("status") == TaskStatus.FAILED.value
                and "insufficient" in (result.get("response") or "").lower()
                for result in prior_results
            )

        if "if previous task failed" in normalized_condition:
            return any(
                result.get("status") == TaskStatus.FAILED.value for result in prior_results
            )

        # Default: run the task
        return True

    def _suggest_alternative_accounts(
        self, phone_number: str, exclude_account_id: Optional[str] = None
    ) -> Optional[str]:
        """Suggest alternative funding sources by inspecting user accounts."""
        try:
            accounts_result = get_user_accounts.invoke(
                {"phone_number": phone_number})
        except Exception as exc:  # pragma: no cover - defensive
            print(f"⚠️ Failed to load accounts for suggestion: {exc}")
            return None

        if not isinstance(accounts_result, dict) or not accounts_result.get("success"):
            return None

        accounts = accounts_result.get("accounts") or []
        suggestion_lines: List[str] = []
        combined_balance = 0.0

        for account in accounts:
            if not account.get("is_active", True):
                continue
            if exclude_account_id and account.get("id") == exclude_account_id:
                continue

            balance_raw = account.get("balance")
            try:
                balance_value = float(balance_raw)
            except (TypeError, ValueError):
                continue

            if balance_value <= 0:
                continue

            bank_name = account.get("bank_name", "Unknown bank")
            account_number = account.get("account_number", "")[-4:]
            suggestion_lines.append(
                f"- {bank_name} ••••{account_number}: ₦{balance_value:,.2f}"
            )
            combined_balance += balance_value

        if not suggestion_lines:
            return None

        suggestion = "I spotted other active accounts you can use:\n"
        suggestion += "\n".join(suggestion_lines)
        suggestion += f"\nCombined available balance: ₦{combined_balance:,.2f}"
        suggestion += "\nLet me know if you want to move funds from any of them."
        return suggestion

    async def execute_task_plan(
        self,
        *,
        phone_number: str,
        message_id: str,
        tasks: List[Dict[str, Any]],
        context: ConversationContext,
        user_message: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Execute each planned task sequentially, respecting dependencies."""
        results: List[Dict[str, Any]] = []
        task_lookup = {task["id"]: task for task in tasks}

        for task in tasks:
            status = task.get("status", TaskStatus.PENDING.value)
            print(f"🔍 Task '{task.get('id')}' status: {status}")

            # If this is a continuation, reset completed tasks so they can be re-executed
            # This allows the agent to process the user's response
            if context.awaiting_clarification and status == TaskStatus.COMPLETED.value:
                print(
                    f"🔄 Resetting completed task '{task.get('id')}' for continuation")
                task["status"] = TaskStatus.PENDING.value
                status = TaskStatus.PENDING.value

            if status not in {TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value}:
                print(
                    f"⏭️  Skipping task '{task.get('id')}' (status: {status})")
                continue

            depends_on = task.get("depends_on") or []
            if any(
                task_lookup.get(dep, {}).get(
                    "status") != TaskStatus.COMPLETED.value
                for dep in depends_on
            ):
                continue

            if not self._should_run_conditional_task(task.get("condition"), results):
                task["status"] = TaskStatus.COMPLETED.value
                results.append(
                    {
                        "task_id": task["id"],
                        "executor": task.get("executor"),
                        "status": TaskStatus.COMPLETED.value,
                        "response": "Condition not met; task skipped.",
                        "skipped": True,
                    }
                )
                continue

            # Safety net: if planner emits an abort task, clear any pending transfer immediately
            if task.get("action") == "abort_transfer":
                try:
                    from apps.core.src.agent.banking.transfer.transfer_agent import TransferAgent
                    agent = TransferAgent()
                    await agent._ensure_checkpointer()
                    config = {"configurable": {"thread_id": f"TransferAgent:{phone_number}"}}
                    checkpoint = await agent.graph.aget_state(config)
                    if checkpoint and checkpoint.values:
                        values = dict(checkpoint.values)
                        values["awaiting_clarification"] = False
                        values["clarification_type"] = None
                        values["waiting_for_confirmation"] = False
                        values["pending_clarification"] = None
                        values["conversation_stage"] = "completed"
                        values["message"] = "GLOBAL_CANCEL"
                        await agent.graph.ainvoke(values, config)
                except Exception as exc:  # pragma: no cover - defensive
                    print(f"⚠️  Failed to clear transfer during abort task: {exc}")

                task["status"] = TaskStatus.COMPLETED.value
                results.append(
                    {
                        "task_id": task["id"],
                        "executor": "system",
                        "status": TaskStatus.COMPLETED.value,
                        "response": "Transfer cancelled. Is there anything else I can help you with?",
                    }
                )
                break

            task["status"] = TaskStatus.IN_PROGRESS.value
            executor = task.get("executor", "query")

            # If this is a continuation (awaiting clarification), use the user's actual message
            # instead of the task instruction, so the agent can parse their response
            print(f"🔍 TASK EXECUTOR DEBUG:")
            print(
                f"   context.awaiting_clarification: {context.awaiting_clarification}")
            print(
                f"   user_message: {user_message[:50] if user_message else None}...")
            print(
                f"   task instruction: {task.get('instruction', '')[:50]}...")

            if context.awaiting_clarification and user_message:
                instruction = user_message
                print(
                    f"🔄 Continuation: Using user's message instead of task instruction: {user_message[:50]}...")
            else:
                instruction = task.get("instruction") or task.get(
                    "description") or ""
                print(f"📝 Using task instruction: {instruction[:50]}...")

            sub_message_id = f"{message_id}:{task['id']}"

            invocation_result: Dict[str, Any]
            if executor in ("tool", "query"):
                invocation_result = await self.agent_invoker.invoke_query_agent(
                    phone_number=phone_number,
                    instruction=instruction,
                    message_id=sub_message_id,
                    context=context,
                )
            elif executor == "transfer":
                invocation_result = await self.agent_invoker.invoke_transfer_agent(
                    phone_number=phone_number,
                    instruction=instruction,
                    message_id=sub_message_id,
                    context=context,
                )
            elif executor == "utility":
                invocation_result = await self.agent_invoker.invoke_utility_agent(
                    phone_number=phone_number,
                    instruction=instruction,
                    message_id=sub_message_id,
                    context=context,
                )
            elif executor == "system":
                task["status"] = TaskStatus.COMPLETED.value
                system_message = task.get(
                    "description") or task.get("instruction", "")
                results.append(
                    {
                        "task_id": task["id"],
                        "executor": "system",
                        "status": TaskStatus.COMPLETED.value,
                        "response": system_message or "System note.",
                    }
                )
                continue
            else:
                task["status"] = TaskStatus.FAILED.value
                failure_message = f"Executor '{executor}' is not supported."
                results.append(
                    {
                        "task_id": task["id"],
                        "executor": executor,
                        "status": TaskStatus.FAILED.value,
                        "response": failure_message,
                    }
                )
                continue

            response_text = invocation_result.get("response", "")
            error = invocation_result.get("error")
            if error:
                task["status"] = TaskStatus.FAILED.value
                results.append(
                    {
                        "task_id": task["id"],
                        "executor": executor,
                        "status": TaskStatus.FAILED.value,
                        "response": response_text,
                        "error": error,
                    }
                )

                # Check for insufficient funds and suggest alternatives
                if "insufficient" in (response_text or "").lower():
                    params = task.get("parameters") or {}
                    exclude_account = params.get("from_account_id")
                    suggestion_text = self._suggest_alternative_accounts(
                        phone_number, exclude_account
                    )
                    if suggestion_text:
                        suggestion_id = f"{task['id']}_suggestions"
                        results.append(
                            {
                                "task_id": suggestion_id,
                                "executor": "system",
                                "status": TaskStatus.COMPLETED.value,
                                "response": suggestion_text,
                                "is_suggestion": True,
                            }
                        )
            else:
                task["status"] = TaskStatus.COMPLETED.value
                results.append(
                    {
                        "task_id": task["id"],
                        "executor": executor,
                        "status": TaskStatus.COMPLETED.value,
                        "response": response_text,
                        "awaiting_clarification": invocation_result.get("awaiting_clarification", False),
                        "clarification_type": invocation_result.get("clarification_type"),
                    }
                )

                # If agent is awaiting clarification, stop execution
                if invocation_result.get("awaiting_clarification"):
                    break

        return tasks, results
