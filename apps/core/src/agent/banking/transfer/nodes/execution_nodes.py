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

        plan: list[dict[str, Any]] = []
        details = state.get("transfer_details", {})
        amount_details = details.get("amount", {}) or {}
        recipients = details.get("recipients") or []
        if not recipients and details.get("recipient"):
            recipients = [details["recipient"]]
            details["recipients"] = recipients
            details["current_recipient_index"] = min(
                details.get("current_recipient_index", 0), len(recipients) - 1
            )
        source_account = details.get("source_account", {}) or {}

        # Handle dependencies
        dependencies = state.get("dependencies", [])
        if "check_balance" in dependencies:
            plan.append({
                "step": "check_balance",
                "tool": "get_account_balance",
                "account_id": source_account.get("account_id"),
            })

        # Handle calculations
        if amount_details.get("needs_calculation"):
            expression = amount_details.get("calculation_expression", "")
            if "%" in expression or "percent" in expression.lower():
                match = re.search(r"(\d+(?:\.\d+)?)\s*%", expression)
                percentage = float(match.group(1)) if match else 10
                plan.append({
                    "step": "calculate_percentage",
                    "percentage": percentage,
                    "base_value": "balance",
                    "account_id": source_account.get("account_id"),
                })

        total_value = amount_details.get("total_value")
        per_value = amount_details.get("per_recipient_value")
        base_value = amount_details.get("value")
        split_strategy = amount_details.get("split_strategy")

        transfer_amounts: list[float | None] = []
        if recipients:
            if split_strategy == "equal" and total_value is not None:
                participant_count = len(recipients)
                if participant_count > 0:
                    even_amount = round(total_value / participant_count, 2)
                    amounts = [even_amount] * participant_count
                    # Adjust final recipient for rounding differences
                    remainder = round(total_value - even_amount * (participant_count - 1), 2)
                    if amounts:
                        amounts[-1] = remainder
                    transfer_amounts = amounts
                    amount_details["per_recipient_value"] = even_amount
                    amount_details["value"] = even_amount
            elif per_value is not None:
                transfer_amounts = [per_value for _ in recipients]
            elif base_value is not None:
                transfer_amounts = [base_value for _ in recipients]
            else:
                transfer_amounts = [recipient.get("allocated_amount") for recipient in recipients]
        else:
            transfer_amounts = [base_value]

        # Build plan steps per recipient
        for idx, recipient in enumerate(recipients or [{}]):
            recipient_amount = None
            if transfer_amounts and idx < len(transfer_amounts):
                recipient_amount = transfer_amounts[idx]
                if recipient is not None:
                    recipient["allocated_amount"] = recipient_amount

            if recipient.get("is_new_beneficiary"):
                plan.append({
                    "step": "validate_account",
                    "recipient_index": idx,
                    "bank_code": recipient.get("bank_code"),
                    "account_number": recipient.get("account_number"),
                })

            plan.append({
                "step": "initiate_transfer",
                "recipient_index": idx,
                "from_account_id": source_account.get("account_id"),
                "to_account_number": recipient.get("account_number"),
                "to_bank_code": recipient.get("bank_code"),
                "amount": recipient_amount,
                "recipient_name": recipient.get("name"),
                "narration": details.get("purpose") or "Transfer",
            })

            if recipient.get("is_new_beneficiary"):
                plan.append({
                    "step": "save_beneficiary",
                    "recipient_index": idx,
                    "name": recipient.get("name"),
                    "account_number": recipient.get("account_number"),
                    "bank_code": recipient.get("bank_code"),
                })

        # Persist normalized metadata
        details["amount"] = amount_details
        details["recipients"] = recipients
        state["transfer_details"] = details

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
        phone_number = state.get("phone_number", "")

        # Execute calculation steps
        for step in plan:
            if step["step"] == "calculate_percentage":
                account_id = step["account_id"]
                balance_result = get_account_balance.invoke(
                    {
                        "phone_number": phone_number,
                        "account_id": account_id,
                    }
                )
                if balance_result.get("success"):
                    balance = balance_result.get("balance", 0)
                    percentage = step["percentage"]
                    calculated_amount = balance * (percentage / 100)
                    details["amount"]["value"] = calculated_amount
                    details["source_account"]["balance"] = balance
                    print(
                        f"💰 Calculated: {percentage}% of ₦{balance:,.2f} = ₦{calculated_amount:,.2f}")

        # Validate business rules
        amount_details = details.get("amount", {}) or {}
        recipients = details.get("recipients") or []
        source_account = details.get("source_account", {})

        transfer_steps = [step for step in plan if step.get("step") == "initiate_transfer"]
        transfer_amounts = [
            step.get("amount") for step in transfer_steps if step.get("amount") is not None
        ]

        if not transfer_amounts:
            errors.append("Transfer amount is required")
        else:
            for amt in transfer_amounts:
                if amt is None:
                    errors.append("Transfer amount is required")
                    continue
                if amt < 100:
                    errors.append("Minimum transfer amount is ₦100")
                if amt > 500000:
                    errors.append("Amount exceeds daily transfer limit of ₦500,000")

        total_transfer_amount = sum(transfer_amounts) if transfer_amounts else 0

        # Check balance
        if source_account.get("balance") is None:
            balance_result = get_account_balance.invoke(
                {
                    "phone_number": phone_number,
                    "account_id": source_account.get("account_id"),
                }
            )
            if balance_result.get("success"):
                source_account["balance"] = balance_result.get("balance", 0)

        if source_account.get("balance") and total_transfer_amount:
            if total_transfer_amount > source_account["balance"]:
                errors.append(
                    f"Insufficient balance. Available: ₦{source_account['balance']:,.2f}, "
                    f"Required: ₦{total_transfer_amount:,.2f}"
                )

        for recipient in recipients or [details.get("recipient", {}) or {}]:
            recipient_name = recipient.get("name", "recipient")
            if not recipient.get("account_number"):
                errors.append(f"Account number is required for {recipient_name}")
            if not recipient.get("bank_code"):
                errors.append(f"Bank selection is required for {recipient_name}")

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

        state["transfer_details"] = details
        return state
