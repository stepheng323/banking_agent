"""Node to handle new transfer intent while a pending transfer is awaiting PIN."""

from typing import Callable

from apps.core.src.agent.orchestrator.state import OrchestratorState
from apps.core.src.agent.banking.transfer.transfer_agent import TransferAgent


class PendingSwitchNode:
    def __init__(self, get_transfer_agent: Callable[[], TransferAgent]):
        self._get_transfer_agent = get_transfer_agent

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        phone_number = state["phone_number"]
        new_instruction = state["message"]

        agent = self._get_transfer_agent()
        await agent._ensure_checkpointer()
        config = {"configurable": {"thread_id": f"TransferAgent:{phone_number}"}}
        checkpoint = await agent.graph.aget_state(config)

        values = (checkpoint and checkpoint.values) or {}
        is_waiting_pin = (
            values.get("awaiting_clarification")
            and values.get("clarification_type") == "pin_confirmation"
        )
        if not is_waiting_pin:
            # No pending transfer; fall back to normal flow
            state["route_fallback"] = True
            return state

        details = values.get("transfer_details", {}) or {}
        amount = details.get("amount", {}) or {}
        recipients = details.get("recipients") or [details.get("recipient", {}) or {}]
        primary = recipients[0] if recipients else {}
        total = amount.get("total_value") or amount.get("value") or 0.0
        bank_name = primary.get("bank_name", "Unknown Bank")
        account_number = primary.get("account_number") or "N/A"

        last4 = "****"
        src_id = (details.get("source_account") or {}).get("account_id")
        for acc in (values.get("user_accounts") or []):
            if acc.get("id") == src_id:
                last4 = (acc.get("account_number") or "")[-4:] or "****"
                break

        question = (
            "You have a pending transfer awaiting PIN.\n\n"
            f"- Pending: ₦{(total or 0):,.2f} → {bank_name} (`{account_number}`) from (...{last4})\n\n"
            "Do you want to continue with the OLD pending transfer or switch to this NEW instruction?\n"
            "Reply 'old' to continue, or 'new' to switch."
        )

        state["response"] = question
        state["awaiting_clarification"] = True
        state["clarification_type"] = "pending_switch_confirmation"
        state["pending_clarification"] = {
            "type": "pending_switch_confirmation",
            "new_instruction": new_instruction,
        }
        # Also store directly in state for easier access
        state["new_instruction"] = new_instruction
        print(f"   📋 Stored new instruction in state: {new_instruction[:50]}...")
        return state


