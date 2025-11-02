"""Task planning service using LLM."""

import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from apps.core.src.agent.models.planner import PlannerOutput
from apps.core.src.agent.models.user_context import UserContext
from apps.core.src.agent.prompts.planner import (
    PLANNER_SYSTEM_PROMPT,
    PLANNER_USER_PROMPT_TEMPLATE,
)
from shared.types.agent_types import TaskStatus


class TaskPlanner:
    """Plans tasks using LLM-based planner."""

    def __init__(self, planner_llm: ChatOpenAI):
        """Initialize with planner LLM."""
        self.planner_llm = planner_llm

    async def plan_tasks(
        self, phone_number: str, message: str, user_context: Optional[UserContext] = None
    ) -> Tuple[str, List[Dict[str, Any]], str, Optional[str]]:
        """Generate a normalized task plan for the user instruction."""
        # Build context string for planner
        context_info = ""
        if user_context:
            beneficiary_names = ', '.join(
                [b.get('name', '') for b in user_context.beneficiaries[:5]])
            if len(user_context.beneficiaries) > 5:
                beneficiary_names += f" (and {len(user_context.beneficiaries) - 5} more)"

            total_balance = user_context.get_total_balance()

            context_info = f"""
USER CONTEXT (use for validation and suggestions):
- Beneficiaries: {beneficiary_names or 'None saved'}
- Accounts: {len(user_context.accounts)} accounts with combined balance ₦{total_balance:,.2f}
- Recent activity: Available for verification
"""

        planner_messages = [
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(
                content=PLANNER_USER_PROMPT_TEMPLATE.format(
                    phone_number=phone_number,
                    user_message=message,
                ) + context_info
            ),
        ]

        try:
            for attempt in range(2):
                planner_result = await self.planner_llm.ainvoke(planner_messages)
                print(
                    f"🔍 Planner result: {json.dumps(planner_result, indent=2) if isinstance(planner_result, dict) else planner_result}")

                if isinstance(planner_result, dict):
                    planner_output = PlannerOutput(**planner_result)
                else:
                    planner_output = planner_result

                tasks: List[Dict[str, Any]] = []
                for task in planner_output.tasks:
                    task_payload = task.dict()
                    status = task_payload.get("status", TaskStatus.PENDING)
                    if isinstance(status, TaskStatus):
                        task_payload["status"] = status.value
                    tasks.append(task_payload)

                print(f"🔍 Tasks: {json.dumps(tasks, indent=2)}")

                if tasks or planner_output.primary_intent == "conversational":
                    return (
                        planner_output.normalized_instruction,
                        tasks,
                        planner_output.primary_intent,
                        planner_output.notes,
                    )

                print("⚠️ Planner produced no tasks; requesting correction.")
                planner_messages.append(
                    HumanMessage(
                        content=(
                            "The previous reply omitted the required 'tasks' array. "
                            "Respond again with valid JSON containing at least one task."
                        )
                    )
                )

            print("⚠️ Planner failed twice to produce tasks.")
            return message, [], "mixed", None
        except Exception as exc:  # pragma: no cover - defensive
            print(f"⚠️ Planner failed: {exc}")
            return message, [], "mixed", None
