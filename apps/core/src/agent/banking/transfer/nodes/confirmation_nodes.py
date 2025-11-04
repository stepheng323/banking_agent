"""Confirmation nodes for the transfer agent."""
from typing import Any, Dict, Any as AnyType

from langchain_core.messages import HumanMessage, AIMessage

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from shared.config.settings import settings


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

        # ALWAYS check for cancel FIRST, before any other processing
        # This prevents overwriting cancel responses regardless of how this node is called
        user_message = state.get("message", "").lower().strip()
        cancel_keywords = ["cancel", "abort", "stop", "nevermind", "never mind", "quit", "disregard"]
        has_cancel_keyword = any(kw in user_message for kw in cancel_keywords)
        
        if has_cancel_keyword:
            # User is trying to cancel - don't overwrite any response
            print("   ⚠️  CANCEL detected in confirmation_agent - skipping all processing")
            return state

        # If conversation is already completed (e.g., cancel was detected), don't overwrite the response
        if state.get("conversation_stage") == "completed":
            print("   ℹ️  Conversation already completed, skipping confirmation")
            return state

        # If we're already awaiting PIN confirmation, do not re-queue the flow
        if state.get("awaiting_clarification") and state.get("clarification_type") == "pin_confirmation":
            state["response"] = "Please authorize the transfer using the secure PIN prompt I sent. Reply 'cancel' to stop."
            state["conversation_stage"] = "confirming"
            state["waiting_for_confirmation"] = True
            return state

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

        fee = self._calculate_transfer_fee(total_amount)
        total_with_fee = total_amount + fee

        source_account_id = source_account.get("account_id")

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

        last_four_digits = source_account_number[-4:] if len(
            source_account_number) >= 4 else "****"

        lines = [
            "*Confirm Your Transfer*",
            "",
            f"Amount: *{self._format_currency(total_amount)}*",
        ]

        primary_recipient = recipients[0] if recipients else {}

        resolved_name = primary_recipient.get("resolved_account_name")
        raw_name = primary_recipient.get("name") or "Recipient"
        recipient_name = resolved_name or raw_name

        bank_name = primary_recipient.get("bank_name", "Unknown Bank")
        account_number = primary_recipient.get("account_number") or "N/A"

        recipient_line = f"To: *{recipient_name}* ({bank_name} - ```{account_number}```)"
        lines.append(recipient_line)

        from_line = f"From: {source_bank_name} (...{last_four_digits})"
        lines.append(from_line)

        if purpose:
            lines.append(f"Narration: _{purpose}_")

        lines.append("")
        lines.append(f"*Fee:* {self._format_currency(fee)}")
        lines.append(f"*Total:* {self._format_currency(total_with_fee)}")
        lines.append("")
        flow_summary = "\n".join(lines)

        phone_number = state.get("phone_number")
        flow_id = getattr(settings, "pin_flow_id", None) or getattr(settings, "onboarding_flow_id", "")
        flow_cta = "Authorize"
        screen_name = "pin_entry"
        header = "Authorize Transfer"
        text_body = flow_summary
        footer = "Secure PIN required to proceed"
        flow_token = state.get("message_id", "")

        flow_action_payload: Dict[str, AnyType] = {
            "screen": screen_name,
            "params": {
                "amount": self._format_currency(total_amount),
                "fee": self._format_currency(fee),
                "total": self._format_currency(total_with_fee),
                "recipient_account": account_number,
                "recipient_bank": bank_name,
                "source_last4": last_four_digits,
            },
        }

        outbox_item: Dict[str, AnyType] = {
            "channel": "whatsapp",
            "type": "flow",
            "to": phone_number,
            "flow": {
                "flow_id": flow_id,
                "flow_cta": flow_cta,
                "screen_name": screen_name,
                "header": header,
                "text_body": text_body,
                "footer": footer,
                "flow_token": flow_token,
                "flow_action_payload": flow_action_payload,
            },
            "meta": {
                "correlation_id": state.get("message_id"),
                "purpose": "pin_confirmation",
            },
        }

        if not state.get("outbox_messages"):
            state["outbox_messages"] = []
        state["outbox_messages"].append(outbox_item)

        state["pending_clarification"] = {
            "type": "pin_confirmation",
            "flow_id": flow_id,
            "flow_token": flow_token,
        }

        state["response"] = "Please authorize the transfer by entering your PIN in the secure prompt I just sent."

        state["conversation_stage"] = "confirming"
        state["waiting_for_confirmation"] = True
        state["awaiting_clarification"] = True
        state["clarification_type"] = "pin_confirmation"

        print("📋 Confirmation (PIN flow) queued to outbox")
        return state

    async def handle_confirmation_response(self, state: TransferState) -> TransferState:
        """Handle user's response to confirmation."""
        print("📝 Handling confirmation response...")

        user_response = state["message"].lower().strip()
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
