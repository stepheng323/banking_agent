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
            elif not state["transfer_details"]["recipient"]:
                state["transfer_details"]["recipient"] = {
                    "is_new_beneficiary": True}

            if "amount" not in state["transfer_details"] or state["transfer_details"]["amount"] is None:
                state["transfer_details"]["amount"] = {
                    "currency": "NGN", "needs_calculation": False}
            elif not state["transfer_details"]["amount"]:
                state["transfer_details"]["amount"] = {
                    "currency": "NGN", "needs_calculation": False}

            if "source_account" not in state["transfer_details"] or state["transfer_details"]["source_account"] is None:
                state["transfer_details"]["source_account"] = {}
            elif not state["transfer_details"]["source_account"]:
                state["transfer_details"]["source_account"] = {}

            if "recipient" in parsed_intent and parsed_intent["recipient"]:
                for key, value in parsed_intent["recipient"].items():
                    if value is not None and value != "":
                        state["transfer_details"]["recipient"][key] = value

            if "amount" in parsed_intent and parsed_intent["amount"]:
                for key, value in parsed_intent["amount"].items():
                    if value is not None and value != "":
                        state["transfer_details"]["amount"][key] = value

            if "source_account" in parsed_intent and parsed_intent["source_account"]:
                for key, value in parsed_intent["source_account"].items():
                    if value is not None and value != "":
                        state["transfer_details"]["source_account"][key] = value

            if "purpose" in parsed_intent:
                state["transfer_details"]["purpose"] = parsed_intent["purpose"]

            # Initialize dependencies list, handling None from checkpoint state
            state["dependencies"] = parsed_intent.get("dependencies") or []

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

        # Initialize messages list, handling None from checkpoint state
        if not state.get("messages"):
            state["messages"] = []
        state["messages"].append(HumanMessage(content=user_message))

        state["conversation_stage"] = "enriching"
        return state
