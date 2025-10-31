# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""Confirmation nodes for the transfer agent."""
from typing import Any

from langchain_core.messages import HumanMessage, AIMessage

from apps.core.src.agent.banking.transfer.transfer_state import TransferState


class ConfirmationNodes:
    """Nodes for confirmation handling."""

    def __init__(self, llm: Any) -> None:
        """Initialize with LLM instance."""
        self.llm = llm

    async def confirmation_agent_node(self, state: TransferState) -> TransferState:
        """Generate confirmation message."""
        print("💬 CONFIRMATION AGENT: Generating confirmation...")

        details = state.get("transfer_details", {})
        amount = details.get("amount", {}).get("value")
        recipient_name = details.get("recipient", {}).get("name")
        bank_name = details.get("recipient", {}).get("bank_name")
        account_number = details.get("recipient", {}).get("account_number")
        source_account_name = details.get("source_account", {}).get("account_name")
        source_balance = details.get("source_account", {}).get("balance")
        purpose = details.get("purpose")

        masked_account = "****" + account_number[-4:] if account_number else "****"

        confirmation_message = f"""Please confirm this transfer:

💸 Amount: ₦{amount:,.2f}
👤 To: {recipient_name}
🏦 Bank: {bank_name} - {masked_account}
📤 From: {source_account_name} (Balance: ₦{source_balance:,.2f})"""

        if purpose:
            confirmation_message += f"\n📝 Purpose: {purpose}"

        new_balance = source_balance - amount if source_balance and amount else 0
        confirmation_message += f"\n\n💰 New balance: ₦{new_balance:,.2f}"
        confirmation_message += "\n\nReply 'confirm' to proceed or tell me what to change."

        if "messages" not in state:
            state["messages"] = []
        state["messages"].append(AIMessage(content=confirmation_message))
        state["response"] = confirmation_message
        state["conversation_stage"] = "confirming"
        state["waiting_for_confirmation"] = True

        print("📋 Confirmation message sent")
        return state

    async def handle_confirmation_response(self, state: TransferState) -> TransferState:
        """Handle user's response to confirmation."""
        print("📝 Handling confirmation response...")

        user_response = state["message"].lower().strip()
        if "messages" not in state:
            state["messages"] = []
        state["messages"].append(HumanMessage(content=user_response))

        confirmation_words = ["confirm", "yes", "proceed",
                              "ok", "continue", "go ahead", "send it"]
        cancel_words = ["cancel", "stop", "no", "abort", "don't", "nevermind"]
        modify_words = ["change", "modify", "update", "different", "wrong"]

        if any(word in user_response for word in confirmation_words):
            print("✅ User confirmed")
            state["conversation_stage"] = "executing"
            state["waiting_for_confirmation"] = False
            return state

        if any(word in user_response for word in cancel_words):
            print("❌ User cancelled")
            state["response"] = "Transfer cancelled. Is there anything else I can help you with?"
            state["messages"].append(AIMessage(content=state["response"]))
            state["conversation_stage"] = "completed"
            state["waiting_for_confirmation"] = False
            return state

        if any(word in user_response for word in modify_words):
            print("🔄 User wants to modify")
            # Simplified: clear amount to trigger re-gathering
            state["transfer_details"]["amount"]["value"] = None
            state["missing_slots"] = ["amount.value"]
            state["conversation_stage"] = "gathering"
            state["waiting_for_confirmation"] = False
            return state

        state["response"] = "I didn't understand. Please reply 'confirm' to proceed, 'cancel' to stop, or tell me what to change."
        state["messages"].append(AIMessage(content=state["response"]))
        return state

