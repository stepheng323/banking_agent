# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""Validation nodes for the transfer agent."""
from typing import Any

from apps.core.src.agent.banking.transfer.transfer_state import TransferState


class ValidationNodes:
    """Nodes for slot validation."""

    def __init__(self, llm: Any) -> None:
        """Initialize with LLM instance."""
        self.llm = llm

    def slot_validator_node(self, state: TransferState) -> TransferState:
        """Validate that all required slots are filled."""
        print("✓ SLOT VALIDATOR: Checking required information...")

        missing = []
        details = state.get("transfer_details", {})

        # Check recipient
        if not details.get("recipient", {}).get("matched_beneficiary_id"):
            if not details.get("recipient", {}).get("account_number"):
                missing.append("recipient.account_number")
            if not details.get("recipient", {}).get("bank_code"):
                missing.append("recipient.bank_code")

        # Check amount
        if details.get("amount", {}).get("needs_calculation"):
            if not details.get("amount", {}).get("source_data"):
                missing.append("amount.source_data")
        elif not details.get("amount", {}).get("value"):
            missing.append("amount.value")

        # Check source account
        accounts = state.get("user_accounts", []) or []
        if len(accounts) > 1:
            if not details.get("source_account", {}).get("account_id"):
                missing.append("source_account.account_id")
        elif len(accounts) == 1:
            account = accounts[0]
            if "source_account" not in details:
                details["source_account"] = {}
            details["source_account"]["account_id"] = account["id"]
            details["source_account"]["account_name"] = account.get(
                "account_name", account.get("bank_name", ""))
            details["source_account"]["balance"] = account.get("balance")

        state["missing_slots"] = missing

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

