# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""Intent parsing nodes for the transfer agent."""
import json
import traceback
from typing import Any

from langchain_core.messages import HumanMessage

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.transfer.prompts import INTENT_PARSER_PROMPT


class IntentNodes:
    """Nodes for intent parsing and initial processing."""

    def __init__(self, llm: Any) -> None:
        """Initialize with LLM instance."""
        self.llm = llm

    async def intent_parser_node(self, state: TransferState) -> TransferState:
        """Extract structured transfer information from user message."""
        print("🧠 INTENT PARSER: Analyzing user message...")

        user_message = state["message"]

        prompt = INTENT_PARSER_PROMPT.format(user_message=user_message)
        messages = [HumanMessage(content=prompt)]

        try:
            response = await self.llm.ainvoke(messages)

            if hasattr(response, "content") and isinstance(response.content, str):
                response_text = response.content
            else:
                raise ValueError("LLM response has no valid content")

            if "```json" in response_text:
                response_text = response_text.split(
                    "```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split(
                    "```")[1].split("```")[0].strip()

            parsed_intent = json.loads(response_text)

            if "transfer_details" not in state or state.get("transfer_details") is None:
                state["transfer_details"] = {
                    "recipient": {"is_new_beneficiary": True},
                    "amount": {"currency": "NGN", "needs_calculation": False},
                    "source_account": {},
                    "purpose": None,
                    "notes": None,
                }

            if "recipient" not in state["transfer_details"] or state["transfer_details"]["recipient"] is None:
                state["transfer_details"]["recipient"] = {
                    "is_new_beneficiary": True}
            if "amount" not in state["transfer_details"] or state["transfer_details"]["amount"] is None:
                state["transfer_details"]["amount"] = {
                    "currency": "NGN", "needs_calculation": False}
            if "source_account" not in state["transfer_details"] or state["transfer_details"]["source_account"] is None:
                state["transfer_details"]["source_account"] = {}

            if "recipient" in parsed_intent and parsed_intent["recipient"]:
                state["transfer_details"]["recipient"].update(
                    parsed_intent["recipient"])

            if "amount" in parsed_intent and parsed_intent["amount"]:
                state["transfer_details"]["amount"].update(
                    parsed_intent["amount"])

            if "source_account" in parsed_intent and parsed_intent["source_account"]:
                state["transfer_details"]["source_account"].update(
                    parsed_intent["source_account"])

            if "purpose" in parsed_intent:
                state["transfer_details"]["purpose"] = parsed_intent["purpose"]

            state["dependencies"] = parsed_intent.get("dependencies", [])

            print(f"✅ Parsed intent: {json.dumps(parsed_intent, indent=2)}")

        except Exception as e:
            print(f"❌ Error parsing intent: {e}")
            print(f"Traceback: {traceback.format_exc()}")
            if "transfer_details" not in state:
                state["transfer_details"] = {
                    "recipient": {"is_new_beneficiary": True},
                    "amount": {"currency": "NGN", "needs_calculation": False},
                    "source_account": {},
                    "purpose": None,
                    "notes": None,
                }

        if "messages" not in state:
            state["messages"] = []
        state["messages"].append(HumanMessage(content=user_message))

        state["conversation_stage"] = "enriching"
        return state
