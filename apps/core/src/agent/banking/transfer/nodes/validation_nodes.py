# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""Validation nodes for the transfer agent."""
from typing import Any

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.tools.account_tools import get_account_balance


class ValidationNodes:
    """Nodes for slot validation."""

    def __init__(self, llm: Any) -> None:
        """Initialize with LLM instance."""
        self.llm = llm

    def slot_validator_node(self, state: TransferState) -> TransferState:
        """Validate that all required slots are filled."""
        print("✓ SLOT VALIDATOR: Checking required information...")

        missing: list[str] = []
        details = state.get("transfer_details", {}) or {}
        amount_details = details.setdefault("amount", {})
        source_account = details.setdefault("source_account", {})
        recipients = details.get("recipients") or []
        phone_number = state.get("phone_number", "")

        # Determine active recipient index based on outstanding requirements
        recipient_missing_map: list[list[str]] = []
        for recipient in recipients:
            recipient_missing: list[str] = []
            if not recipient.get("matched_beneficiary_id"):
                if not recipient.get("account_number"):
                    recipient_missing.append("recipient.account_number")
                if not recipient.get("bank_code"):
                    recipient_missing.append("recipient.bank_code")
            recipient_missing_map.append(recipient_missing)

        active_index = details.get("current_recipient_index", 0)
        for idx, recipient_missing in enumerate(recipient_missing_map):
            if recipient_missing:
                active_index = idx
                break
        else:
            if recipients:
                active_index = min(active_index, len(recipients) - 1)
            else:
                active_index = 0

        if recipients:
            recipient_details = recipients[active_index]
        else:
            recipient_details = details.setdefault("recipient", {})

        details["current_recipient_index"] = active_index
        details["recipient"] = recipient_details

        if recipients:
            pending_slots = recipient_missing_map[active_index]
            missing.extend(pending_slots)
            if "recipient.account_number" not in pending_slots and "recipient.bank_code" not in pending_slots:
                next_index = active_index + 1
                if next_index < len(recipients):
                    details["current_recipient_index"] = next_index
                    details["recipient"] = recipients[next_index]
                    return self.slot_validator_node(state)
        else:
            if not recipient_details.get("matched_beneficiary_id"):
                if not recipient_details.get("account_number"):
                    missing.append("recipient.account_number")
                if not recipient_details.get("bank_code"):
                    missing.append("recipient.bank_code")

        # Check source account early so downstream calculations have the ID
        accounts = state.get("user_accounts", []) or []
        if len(accounts) > 1:
            if not source_account.get("account_id"):
                missing.append("source_account.account_id")
        elif len(accounts) == 1:
            account = accounts[0]
            source_account["account_id"] = account["id"]
            source_account["account_name"] = account.get(
                "account_name", account.get("bank_name", "")
            )
            source_account["balance"] = account.get("balance")

        # Check amount
        split_strategy = amount_details.get("split_strategy")
        if amount_details.get("needs_calculation"):
            if split_strategy == "equal" and amount_details.get("value"):
                amount_details["needs_calculation"] = False
            else:
                account_id = source_account.get("account_id")
                if not account_id:
                    if "source_account.account_id" not in missing:
                        missing.append("source_account.account_id")
                elif not amount_details.get("source_data"):
                    balance_payload = {
                        "phone_number": phone_number,
                        "account_id": account_id,
                    }
                    balance_result = get_account_balance.invoke(balance_payload)
                    if balance_result.get("success"):
                        amount_details["source_data"] = balance_result
                        if balance_result.get("balance") is not None:
                            source_account["balance"] = balance_result.get("balance")
                    else:
                        amount_details["source_data"] = {
                            "success": False,
                            "error": balance_result.get("error", "Could not retrieve balance"),
                        }
                        missing.append("amount.source_data")
        elif not amount_details.get("value"):
            account_id = source_account.get("account_id")
            missing.append("amount.value")

        if recipients:
            recipients[active_index] = recipient_details
            details["recipients"] = recipients

        state["missing_slots"] = missing
        state["transfer_details"] = details

        if missing:
            print(f"⚠️ Missing slots: {missing}")
            state["conversation_stage"] = "gathering"
        elif state.get("clarifications_needed"):
            print(f"⚠️ Clarifications needed")
            state["conversation_stage"] = "gathering"
        else:
            print("✅ All slots filled")
            state["conversation_stage"] = "planning"

        return state
