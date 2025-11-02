# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""Execution planning and validation nodes for the transfer agent."""
import re
from typing import Any

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.tools.account_tools import get_account_balance


class ExecutionNodes:
    """Nodes for execution planning and pre-validation."""

    def __init__(self, llm: Any) -> None:
        """Initialize with LLM instance."""
        self.llm = llm

    async def executor_planner_node(self, state: TransferState) -> TransferState:
        """Create execution plan for the transfer."""
        print("📋 EXECUTOR PLANNER: Creating execution plan...")

        plan = []
        details = state.get("transfer_details", {})

        # Handle dependencies
        dependencies = state.get("dependencies", [])
        if "check_balance" in dependencies:
            plan.append({
                "step": "check_balance",
                "tool": "get_account_balance",
                "account_id": details.get("source_account", {}).get("account_id"),
            })

        # Handle calculations
        if details.get("amount", {}).get("needs_calculation"):
            expression = details.get("amount", {}).get(
                "calculation_expression", "")
            if "%" in expression or "percent" in expression.lower():
                match = re.search(r"(\d+(?:\.\d+)?)\s*%", expression)
                percentage = float(match.group(1)) if match else 10
                plan.append({
                    "step": "calculate_percentage",
                    "percentage": percentage,
                    "base_value": "balance",
                    "account_id": details.get("source_account", {}).get("account_id"),
                })

        # Validate new beneficiary
        if details.get("recipient", {}).get("is_new_beneficiary"):
            plan.append({
                "step": "validate_account",
                "bank_code": details.get("recipient", {}).get("bank_code"),
                "account_number": details.get("recipient", {}).get("account_number"),
            })

        # Main transfer
        plan.append({
            "step": "initiate_transfer",
            "from_account_id": details.get("source_account", {}).get("account_id"),
            "to_account_number": details.get("recipient", {}).get("account_number"),
            "to_bank_code": details.get("recipient", {}).get("bank_code"),
            "amount": details.get("amount", {}).get("value"),
            "narration": details.get("purpose") or "Transfer",
        })

        # Save beneficiary if new
        if details.get("recipient", {}).get("is_new_beneficiary"):
            plan.append({
                "step": "save_beneficiary",
                "name": details.get("recipient", {}).get("name"),
                "account_number": details.get("recipient", {}).get("account_number"),
                "bank_code": details.get("recipient", {}).get("bank_code"),
            })

        state["execution_plan"] = plan
        print(f"📝 Plan created with {len(plan)} steps")
        state["conversation_stage"] = "validating"
        return state

    async def pre_validator_node(self, state: TransferState) -> TransferState:
        """Validate transfer against business rules."""
        print("✓ PRE-VALIDATOR: Checking business rules...")

        errors = []
        details = state.get("transfer_details", {})
        plan = state.get("execution_plan", [])

        # Execute calculation steps
        for step in plan:
            if step["step"] == "calculate_percentage":
                account_id = step["account_id"]
                balance_result = get_account_balance.invoke(
                    {"account_id": account_id})
                if balance_result.get("success"):
                    balance = balance_result.get("balance", 0)
                    percentage = step["percentage"]
                    calculated_amount = balance * (percentage / 100)
                    details["amount"]["value"] = calculated_amount
                    details["source_account"]["balance"] = balance
                    print(
                        f"💰 Calculated: {percentage}% of ₦{balance:,.2f} = ₦{calculated_amount:,.2f}")

        # Validate business rules
        amount = details.get("amount", {}).get("value")
        source_account = details.get("source_account", {})

        if not amount:
            errors.append("Transfer amount is required")
        elif amount < 100:
            errors.append("Minimum transfer amount is ₦100")
        elif amount > 500000:
            errors.append("Amount exceeds daily transfer limit of ₦500,000")

        # Check balance
        if source_account.get("balance") is None:
            balance_result = get_account_balance.invoke(
                {"account_id": source_account.get("account_id")})
            if balance_result.get("success"):
                source_account["balance"] = balance_result.get("balance", 0)

        if source_account.get("balance") and amount:
            if amount > source_account["balance"]:
                errors.append(
                    f"Insufficient balance. Available: ₦{source_account['balance']:,.2f}, "
                    f"Required: ₦{amount:,.2f}"
                )

        if not details.get("recipient", {}).get("account_number"):
            errors.append("Recipient account number is required")
        if not details.get("recipient", {}).get("bank_code"):
            errors.append("Recipient bank is required")

        state["validation_result"] = {
            "valid": len(errors) == 0, "errors": errors}

        if errors:
            print(f"❌ Validation failed: {errors}")
            error_message = "I can't complete this transfer:\n" + \
                "\n".join(f"• {e}" for e in errors)
            state["response"] = error_message
            state["conversation_stage"] = "completed"
            state["awaiting_clarification"] = False
            state["clarification_type"] = None
        else:
            print("✅ Validation passed")
            state["conversation_stage"] = "confirming"

        return state
