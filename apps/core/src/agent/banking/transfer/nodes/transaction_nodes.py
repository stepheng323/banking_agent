# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""Transaction execution nodes for the transfer agent."""
from typing import Any, Dict, Optional
from datetime import datetime

from langchain_core.messages import AIMessage

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.tools.account_tools import get_user_accounts, verify_account_number


class TransactionNodes:
    """Nodes for executing transactions."""

    def __init__(self, llm: Any) -> None:
        """Initialize with LLM instance."""
        self.llm = llm

    def _initiate_money_transfer(
        self,
        phone_number: str,
        from_account_id: str,
        to_account_number: str,
        amount: float,
        reason: Optional[str] = None,
        recipient_name: Optional[str] = None,
        recipient_bank_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """Initiate a money transfer to another account."""
        try:
            # Validation
            if amount <= 0:
                return {"success": False, "error": "Amount must be greater than zero"}

            if amount > 1000000:  # 1M limit
                return {"success": False, "error": "Amount exceeds transfer limit of ₦1,000,000"}

            accounts_result = get_user_accounts.invoke(
                {"phone_number": phone_number})
            if not accounts_result["success"]:
                return {"success": False, "error": "Could not verify source account"}

            from_account = next(
                (a for a in accounts_result["accounts"]
                 if a["id"] == from_account_id),
                None
            )

            if not from_account:
                return {"success": False, "error": "Source account not found"}

            # Check balance
            if from_account["balance"] < amount:
                return {
                    "success": False,
                    "error": f"Insufficient balance. Available: ₦{from_account['balance']:,.2f}"
                }

            # Calculate fees
            fee = 0.0
            if amount > 50000:
                fee = 50.0

            total_debit = amount + fee

            if from_account["balance"] < total_debit:
                return {
                    "success": False,
                    "error": f"Insufficient balance including fee. Required: ₦{total_debit:,.2f}"
                }

            transaction_id = f"TXN{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

            # TODO: Call actual banking API here
            # Simulate successful transfer
            new_balance = from_account["balance"] - total_debit

            return {
                "success": True,
                "transaction_id": transaction_id,
                "status": "completed",
                "amount": amount,
                "fee": fee,
                "total_debit": total_debit,
                "from_account": from_account["account_number"],
                "from_bank": from_account["bank_name"],
                "to_account": to_account_number,
                "to_bank": recipient_bank_code or "Unknown",
                "recipient_name": recipient_name or "Unknown",
                "reason": reason or "Transfer",
                "new_balance": new_balance,
                "currency": "NGN",
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            return {"success": False, "error": f"Transfer failed: {str(e)}"}

    def _save_beneficiary(
        self,
        phone_number: str,
        name: str,
        account_number: str,
        bank_code: str
    ) -> Dict[str, Any]:
        """Save a new beneficiary for future transfers."""
        try:
            # Verify account first
            verify_result = verify_account_number.invoke({
                "account_number": account_number,
                "bank_code": bank_code
            })

            if not verify_result["success"]:
                return {"success": False, "error": "Could not verify account details"}

            return {
                "success": True,
                "beneficiary_id": f"ben_{datetime.now().timestamp()}",
                "name": name,
                "account_number": account_number,
                "bank_name": verify_result["bank_name"],
                "message": f"{name} has been saved as a beneficiary"
            }

        except Exception as e:
            return {"success": False, "error": f"Failed to save beneficiary: {str(e)}"}

    async def transaction_executor_node(self, state: TransferState) -> TransferState:
        """Execute the transfer transaction."""
        print("⚡ TRANSACTION EXECUTOR: Executing transfer...")

        details = state.get("transfer_details", {})

        try:
            result = self._initiate_money_transfer(
                phone_number=state["phone_number"],
                from_account_id=details.get(
                    "source_account", {}).get("account_id"),
                to_account_number=details.get(
                    "recipient", {}).get("account_number"),
                amount=details.get("amount", {}).get("value"),
                reason=details.get("purpose") or "Transfer",
                recipient_name=details.get("recipient", {}).get("name"),
                recipient_bank_code=details.get(
                    "recipient", {}).get("bank_code"),
            )

            if result.get("success"):
                print("✅ Transfer successful")

                # Save new beneficiary
                saved_msg = ""
                if details.get("recipient", {}).get("is_new_beneficiary"):
                    try:
                        save_result = self._save_beneficiary(
                            phone_number=state["phone_number"],
                            name=details.get("recipient", {}).get("name"),
                            account_number=details.get(
                                "recipient", {}).get("account_number"),
                            bank_code=details.get(
                                "recipient", {}).get("bank_code"),
                        )
                        if save_result.get("success"):
                            saved_msg = f"\n\n✅ I've saved '{details.get('recipient', {}).get('name')}' for future transfers."
                    except Exception as e:
                        print(f"Warning: Could not save beneficiary: {e}")

                success_message = f"""✅ Transfer successful!

₦{details.get('amount', {}).get('value', 0):,.2f} sent to {details.get('recipient', {}).get('name')}
🏦 {details.get('recipient', {}).get('bank_name')} - ****{details.get('recipient', {}).get('account_number', '')[-4:]}
📋 Reference: {result.get('transaction_id', 'N/A')}
💰 New balance: ₦{result.get('new_balance', 0):,.2f}{saved_msg}"""

                if "messages" not in state:
                    state["messages"] = []
                state["messages"].append(AIMessage(content=success_message))
                state["response"] = success_message

            else:
                error_message = f"""❌ Transfer failed: {result.get('error', 'Unknown error')}
Please try again or contact support if the problem persists."""
                if "messages" not in state:
                    state["messages"] = []
                state["messages"].append(AIMessage(content=error_message))
                state["response"] = error_message

        except Exception as e:
            print(f"❌ Exception during transfer: {e}")
            error_message = f"""❌ An error occurred: {str(e)}
Please try again or contact support if the problem persists."""
            if "messages" not in state:
                state["messages"] = []
            state["messages"].append(AIMessage(content=error_message))
            state["response"] = error_message

        state["conversation_stage"] = "completed"
        return state
