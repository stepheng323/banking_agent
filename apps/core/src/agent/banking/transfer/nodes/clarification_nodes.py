# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""Clarification nodes for the transfer agent."""
import json
from typing import Any

from langchain_core.messages import HumanMessage, AIMessage

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.transfer.prompts import CLARIFICATION_PROMPTS


class ClarificationNodes:
    """Nodes for handling clarifications and user responses."""

    def __init__(self, llm: Any) -> None:
        """Initialize with LLM instance."""
        self.llm = llm

    async def clarification_agent_node(self, state: TransferState) -> TransferState:
        """Ask questions to fill missing information."""
        print("❓ CLARIFICATION AGENT: Generating question...")

        question = "I need more information to complete your transfer."

        # Handle clarifications first
        if state.get("clarifications_needed"):
            clarification = state["clarifications_needed"][0]

            if clarification["type"] == "ambiguous_recipient":
                options = clarification["options"]
                options_text = "\n".join([
                    f"{i+1}) {opt['name']} ({opt['bank_name']} ****{opt['account_number'][-4:]})"
                    for i, opt in enumerate(options)
                ])

                prompt = CLARIFICATION_PROMPTS["ambiguous_recipient"].format(
                    options=options_text)
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                if hasattr(response, "content") and isinstance(response.content, str):
                    question = response.content
                else:
                    question = f"Which recipient did you mean from these options?\n{options_text}"

                state["pending_clarification"] = {
                    "type": "ambiguous_recipient", "options": options}

        # Handle missing slots
        elif state.get("missing_slots"):
            missing_slot = state["missing_slots"][0]

            if missing_slot == "recipient.account_number":
                recipient_name = state.get("transfer_details", {}).get("recipient", {}).get(
                    "name", "the recipient")
                prompt = CLARIFICATION_PROMPTS["recipient.account_number"].format(
                    recipient_name=recipient_name)
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                if hasattr(response, "content") and isinstance(response.content, str):
                    question = response.content
                else:
                    question = f"I don't have '{recipient_name}' saved. What's their account number?"
                state["pending_clarification"] = {
                    "type": "recipient.account_number"}

            elif missing_slot == "recipient.bank_code":
                prompt = CLARIFICATION_PROMPTS["recipient.bank_code"]
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                if hasattr(response, "content") and isinstance(response.content, str):
                    question = response.content
                else:
                    question = "Which bank is this account with?"
                state["pending_clarification"] = {
                    "type": "recipient.bank_code"}

            elif missing_slot == "amount.value":
                recipient_name = state.get("transfer_details", {}).get("recipient", {}).get(
                    "name", "")
                prompt = CLARIFICATION_PROMPTS["amount.value"].format(
                    recipient_name=recipient_name)
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                if hasattr(response, "content") and isinstance(response.content, str):
                    question = response.content
                else:
                    question = f"How much would you like to send to {recipient_name}?"
                state["pending_clarification"] = {"type": "amount.value"}

            elif missing_slot == "source_account.account_id":
                accounts = state.get("user_accounts", []) or []
                accounts_text = "\n".join([
                    f"{i+1}) {acc.get('account_name', acc.get('bank_name', 'Account'))} (Balance: ₦{acc.get('balance', 0):,.2f})"
                    for i, acc in enumerate(accounts)
                ])
                prompt = CLARIFICATION_PROMPTS["source_account.account_id"].format(
                    accounts=accounts_text)
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                if hasattr(response, "content") and isinstance(response.content, str):
                    question = response.content
                else:
                    question = f"Which account should I send from?\n{accounts_text}"
                state["pending_clarification"] = {
                    "type": "source_account.account_id", "options": accounts}

        if "messages" not in state:
            state["messages"] = []
        state["messages"].append(AIMessage(content=question))
        state["response"] = question
        state["conversation_stage"] = "gathering"
        state["waiting_for_user_response"] = True

        print(f"💬 Question: {question}")
        return state

    async def parse_clarification_response(self, state: TransferState) -> TransferState:
        """Parse user's response to clarification question."""
        print("📝 Parsing user response...")

        user_response = state["message"]
        pending = state.get("pending_clarification", {})
        clarification_type = pending.get("type")

        # Add user message to history
        if "messages" not in state:
            state["messages"] = []
        state["messages"].append(HumanMessage(content=user_response))

        if clarification_type == "ambiguous_recipient":
            options = pending["options"]
            parse_prompt = f"""Parse the user's selection from these options:
{json.dumps(options, indent=2)}
User response: "{user_response}"
Extract the selected option index (0-based). Return JSON: {{"selected_index": <number>}}
Return only valid JSON."""
            response = await self.llm.ainvoke([HumanMessage(content=parse_prompt)])
            
            # Handle response content safely
            if not hasattr(response, "content") or not isinstance(response.content, str):
                print(f"Error: Invalid response from LLM")
                return state
            response_text = response.content
            
            if "```json" in response_text:
                response_text = response_text.split(
                    "```json")[1].split("```")[0].strip()

            try:
                parsed = json.loads(response_text)
                selected_index = parsed["selected_index"]
                if 0 <= selected_index < len(options):
                    selected = options[selected_index]
                    state["transfer_details"]["recipient"].update({
                        "matched_beneficiary_id": selected["id"],
                        "name": selected["name"],
                        "account_number": selected["account_number"],
                        "bank_code": selected["bank_code"],
                        "bank_name": selected["bank_name"],
                        "confidence_score": selected["confidence_score"],
                        "is_new_beneficiary": False,
                    })
                    state["clarifications_needed"] = [
                        c for c in state.get("clarifications_needed", [])
                        if c["type"] != "ambiguous_recipient"
                    ]
            except Exception as e:
                print(f"Error parsing selection: {e}")

        elif clarification_type == "recipient.account_number":
            parse_prompt = f"""Extract the account number from: "{user_response}"
Account numbers are typically 10 digits. Return JSON: {{"account_number": "<number>"}}"""
            response = await self.llm.ainvoke([HumanMessage(content=parse_prompt)])
            
            # Handle response content safely
            if not hasattr(response, "content") or not isinstance(response.content, str):
                print(f"Error: Invalid response from LLM")
                return state
            response_text = response.content
            
            if "```json" in response_text:
                response_text = response_text.split(
                    "```json")[1].split("```")[0].strip()
            try:
                parsed = json.loads(response_text)
                state["transfer_details"]["recipient"]["account_number"] = parsed["account_number"]
                state["missing_slots"] = [s for s in state.get(
                    "missing_slots", []) if s != "recipient.account_number"]
            except Exception as e:
                print(f"Error parsing account number: {e}")

        elif clarification_type == "recipient.bank_code":
            # Simplified bank matching
            bank_map = {
                "gtb": "058", "gtbank": "058",
                "first": "011", "first bank": "011",
                "access": "044", "access bank": "044",
                "zenith": "057", "zenith bank": "057",
            }
            response_lower = user_response.lower()
            bank_code = None
            bank_name = None
            for key, code in bank_map.items():
                if key in response_lower:
                    bank_code = code
                    bank_name = key.title() + " Bank"
                    break
            if bank_code:
                state["transfer_details"]["recipient"]["bank_code"] = bank_code
                state["transfer_details"]["recipient"]["bank_name"] = bank_name
                state["missing_slots"] = [s for s in state.get(
                    "missing_slots", []) if s != "recipient.bank_code"]

        elif clarification_type == "amount.value":
            parse_prompt = f"""Extract amount from: "{user_response}"
Handle formats like: "₦5000", "5000", "5k", "5,000". Return JSON: {{"amount": <number>}}"""
            response = await self.llm.ainvoke([HumanMessage(content=parse_prompt)])
            
            # Handle response content safely
            if not hasattr(response, "content") or not isinstance(response.content, str):
                print(f"Error: Invalid response from LLM")
                return state
            response_text = response.content
            
            if "```json" in response_text:
                response_text = response_text.split(
                    "```json")[1].split("```")[0].strip()
            try:
                parsed = json.loads(response_text)
                state["transfer_details"]["amount"]["value"] = float(
                    parsed["amount"])
                state["missing_slots"] = [s for s in state.get(
                    "missing_slots", []) if s != "amount.value"]
            except Exception as e:
                print(f"Error parsing amount: {e}")

        elif clarification_type == "source_account.account_id":
            options = pending["options"]
            parse_prompt = f"""Parse account selection from: "{user_response}"
Options: {json.dumps(options, indent=2)}
Return JSON: {{"selected_index": <number>}}"""
            response = await self.llm.ainvoke([HumanMessage(content=parse_prompt)])
            
            # Handle response content safely
            if not hasattr(response, "content") or not isinstance(response.content, str):
                print(f"Error: Invalid response from LLM")
                return state
            response_text = response.content
            
            if "```json" in response_text:
                response_text = response_text.split(
                    "```json")[1].split("```")[0].strip()
            try:
                parsed = json.loads(response_text)
                selected_index = parsed["selected_index"]
                if 0 <= selected_index < len(options):
                    selected = options[selected_index]
                    state["transfer_details"]["source_account"].update({
                        "account_id": selected["id"],
                        "account_name": selected.get("account_name", selected.get("bank_name", "")),
                        "balance": selected.get("balance"),
                    })
                    state["missing_slots"] = [s for s in state.get(
                        "missing_slots", []) if s != "source_account.account_id"]
            except Exception as e:
                print(f"Error parsing account selection: {e}")

        state["pending_clarification"] = None
        state["waiting_for_user_response"] = False
        return state

