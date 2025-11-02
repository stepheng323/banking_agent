"""Response formatting service using LLM."""

import json
from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from apps.core.src.agent.prompts.planner import FORMATTER_SYSTEM_PROMPT


class ResponseFormatter:
    """Formats final responses using LLM."""

    def __init__(self, formatter_llm: ChatOpenAI):
        """Initialize with formatter LLM."""
        self.formatter_llm = formatter_llm

    async def format_final_response(
        self,
        *,
        phone_number: str,
        original_message: str,
        normalized_instruction: str,
        task_results: List[Dict[str, Any]],
        planner_notes: Optional[str] = None,
    ) -> str:
        """Use the formatter LLM to produce the final user-facing response."""
        if not task_results:
            return "I'm sorry, I couldn't process your request."

        outcome_summaries = []
        for result in task_results:
            summary = {
                "task_id": result.get("task_id"),
                "executor": result.get("executor"),
                "status": result.get("status"),
                "response": result.get("response"),
                "awaiting_clarification": result.get("awaiting_clarification", False),
                "skipped": result.get("skipped", False),
            }
            outcome_summaries.append(summary)

        formatter_messages = [
            SystemMessage(content=FORMATTER_SYSTEM_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "user_phone": phone_number,
                        "original_message": original_message,
                        "normalized_instruction": normalized_instruction,
                        "planner_notes": planner_notes,
                        "task_outcomes": outcome_summaries,
                    },
                    default=str,
                )
            ),
        ]

        try:
            formatter_response = await self.formatter_llm.ainvoke(formatter_messages)
            if hasattr(formatter_response, "content") and isinstance(
                formatter_response.content, str
            ):
                return formatter_response.content
            if isinstance(formatter_response, str):
                return formatter_response
        except Exception as exc:  # pragma: no cover - defensive
            print(f"⚠️ Formatter failed: {exc}")

        # Fallback: join responses
        joined = "\n".join(
            result.get("response") or "" for result in task_results if result.get("response")
        )
        return joined or "I've recorded your request, but I need a moment to process it."
