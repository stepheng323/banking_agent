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

    def _calculate_transfer_fee(self, amount: float) -> float:
        """Calculate transfer fee based on amount."""
        if amount > 50000:
            return 50.0
        return 0.0

    def _format_currency(self, amount: float) -> str:
        """Format currency amount."""
        return f"₦{amount:,.2f}"

    async def confirmation_agent_node(self, state: TransferState) -> TransferState:
        """Generate confirmation message."""
        print("💬 CONFIRMATION AGENT: Generating confirmation...")

        details = state.get("transfer_details", {})
        amount_details = details.get("amount", {}) or {}
        recipients = details.get("recipients") or [
            details.get("recipient", {}) or {}]
        source_account = details.get("source_account", {}) or {}
        purpose = details.get("purpose") or details.get("notes")

        per_amount = amount_details.get("value")
        total_amount = amount_details.get("total_value") or (
            per_amount *
            len(recipients) if per_amount and recipients else per_amount
        )
        total_amount = total_amount or 0.0

        # Calculate fee and total
        fee = self._calculate_transfer_fee(total_amount)
        total_with_fee = total_amount + fee

        # Get source account details
        source_account_id = source_account.get("account_id")

        # Fetch full account details from user_accounts if we have account_id
        source_account_number = ""
        source_bank_name = source_account.get("account_name") or ""

        if source_account_id:
            user_accounts = state.get("user_accounts", []) or []
            source_account_obj = next(
                (acc for acc in user_accounts if acc.get("id") == source_account_id),
                None
            )
            if source_account_obj:
                source_account_number = source_account_obj.get(
                    "account_number", "")
                source_bank_name = source_account_obj.get(
                    "bank_name", "") or source_account_obj.get("account_name", "")

        # Get last 4 digits of source account
        last_four_digits = source_account_number[-4:] if len(
            source_account_number) >= 4 else "****"

        # Build confirmation message
        lines = [
            "*Confirm Your Transfer*",
            "",
            f"Amount: *{self._format_currency(total_amount)}*",
        ]

        # Handle recipients (for multi-recipient transfers, show first recipient in main format)
        # In WhatsApp, we typically show the primary recipient in the summary
        primary_recipient = recipients[0] if recipients else {}

        resolved_name = primary_recipient.get("resolved_account_name")
        raw_name = primary_recipient.get("name") or "Recipient"
        recipient_name = resolved_name or raw_name

        bank_name = primary_recipient.get("bank_name", "Unknown Bank")
        account_number = primary_recipient.get("account_number") or "N/A"

        # Format recipient line: To: *Name* (Bank - ```account```)
        recipient_line = f"To: *{recipient_name}* ({bank_name} - ```{account_number}```)"
        lines.append(recipient_line)

        # Format source line: From: Bank (...last4digits)
        from_line = f"From: {source_bank_name} (...{last_four_digits})"
        lines.append(from_line)

        # Add narration if exists
        if purpose:
            lines.append(f"Narration: _{purpose}_")

        lines.append("")
        lines.append(f"*Fee:* {self._format_currency(fee)}")
        lines.append(f"*Total:* {self._format_currency(total_with_fee)}")
        lines.append("")
        lines.append(
            "Tap the authorize button below, to enter your transaction pin.\n")

        confirmation_message = "\n".join(lines)

        # Initialize messages list, handling None from checkpoint state
        if not state.get("messages"):
            state["messages"] = []
        state["messages"].append(AIMessage(content=confirmation_message))
        state["response"] = confirmation_message
        state["conversation_stage"] = "confirming"
        state["waiting_for_confirmation"] = True

        # Set orchestrator conversation tracking flags
        state["awaiting_clarification"] = True
        state["clarification_type"] = "confirmation"

        print("📋 Confirmation message sent")
        return state

    async def handle_confirmation_response(self, state: TransferState) -> TransferState:
        """Handle user's response to confirmation."""
        print("📝 Handling confirmation response...")

        user_response = state["message"].lower().strip()
        # Initialize messages list, handling None from checkpoint state
        if not state.get("messages"):
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
            state["awaiting_clarification"] = False
            state["clarification_type"] = None
            return state

        if any(word in user_response for word in cancel_words):
            print("❌ User cancelled")
            state["response"] = "Transfer cancelled. Is there anything else I can help you with?"
            # Initialize messages list, handling None from checkpoint state
            if not state.get("messages"):
                state["messages"] = []
            state["messages"].append(AIMessage(content=state["response"]))
            state["conversation_stage"] = "completed"
            state["waiting_for_confirmation"] = False
            state["awaiting_clarification"] = False
            state["clarification_type"] = None
            return state

        if any(word in user_response for word in modify_words):
            print("🔄 User wants to modify")
            state["transfer_details"]["amount"]["value"] = None
            state["missing_slots"] = ["amount.value"]
            state["conversation_stage"] = "gathering"
            state["waiting_for_confirmation"] = False
            return state

        state["response"] = "I didn't understand. Please reply 'confirm' to proceed, 'cancel' to stop, or tell me what to change."
        # Initialize messages list, handling None from checkpoint state
        if not state.get("messages"):
            state["messages"] = []
        state["messages"].append(AIMessage(content=state["response"]))
        return state
