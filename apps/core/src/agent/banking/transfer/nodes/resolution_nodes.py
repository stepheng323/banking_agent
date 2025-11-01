"""Account resolution nodes for verifying recipient bank accounts via Flutterwave."""
from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.tools.flutterwave_client import FlutterwaveClient


class ResolutionNodes:
    """Nodes for resolving and verifying bank account details."""

    def __init__(self):
        """Initialize resolution nodes with Flutterwave client."""
        try:
            self.flutterwave_client = FlutterwaveClient()
        except ValueError as e:
            print(f"⚠️  Warning: Flutterwave client not initialized: {e}")
            self.flutterwave_client = None

    async def account_resolver_node(self, state: TransferState) -> TransferState:
        """
        Resolve recipient account details via Flutterwave API.

        This node:
        1. Takes account_number and bank_code from transfer_details
        2. Calls Flutterwave Account Resolution API
        3. Stores resolved account name in transfer_details
        4. Handles errors and routes accordingly
        """
        print("🔍 ACCOUNT RESOLVER: Verifying recipient account details...")

        if not self.flutterwave_client:
            error_msg = "Account verification service is not configured. Please contact support."
            state["response"] = error_msg
            state["conversation_stage"] = "completed"
            state["awaiting_clarification"] = False
            state["clarification_type"] = None
            print(f"❌ {error_msg}")
            return state

        transfer_details = state.get("transfer_details", {})
        recipient = transfer_details.get("recipient", {})

        account_number = recipient.get("account_number")
        bank_code = recipient.get("bank_code")

        if not account_number or not bank_code:
            print("⚠️  Missing account_number or bank_code - skipping resolution")
            # If missing, route back to gathering
            state["conversation_stage"] = "gathering"
            return state

        # Check if already resolved (avoid duplicate API calls)
        if recipient.get("resolved_account_name"):
            print(
                f"✅ Account already resolved: {recipient.get('resolved_account_name')}")
            state["conversation_stage"] = "planning"
            return state

        # Call Flutterwave API
        result = await self.flutterwave_client.resolve_account(
            account_number=account_number, bank_code=bank_code
        )

        if result.get("success"):
            resolved_name = result.get("account_name", "").strip()
            if resolved_name:
                # Store resolved account name
                recipient["resolved_account_name"] = resolved_name
                transfer_details["recipient"] = recipient
                state["transfer_details"] = transfer_details

                print(f"✅ Account resolved: {resolved_name}")
                print(
                    f"   Account: ****{account_number[-4:]} | Bank: {recipient.get('bank_name', bank_code)}")

                # Continue to planning
                state["conversation_stage"] = "planning"
            else:
                # API returned success but no account name
                error_msg = "Could not retrieve account holder name. Please verify the account number and bank code."
                print(f"⚠️  {error_msg}")
                state["response"] = error_msg
                state["conversation_stage"] = "gathering"
                state["awaiting_clarification"] = True
                state["clarification_type"] = "account_verification_failed"
                # Add to missing slots to trigger clarification
                if not state.get("missing_slots"):
                    state["missing_slots"] = []
                state["missing_slots"].append("recipient.account_verification")
        else:
            # Resolution failed
            error = result.get("error", "Account verification failed")
            error_msg = f"❌ Account verification failed: {error}\n\nPlease double-check the account number and bank code, then try again."

            print(f"❌ Account resolution failed: {error}")
            print(f"   Account: {account_number} | Bank: {bank_code}")

            state["response"] = error_msg
            state["conversation_stage"] = "gathering"
            state["awaiting_clarification"] = True
            state["clarification_type"] = "account_verification_failed"

            # Clear the account details to force re-entry
            recipient.pop("account_number", None)
            recipient.pop("bank_code", None)
            recipient.pop("resolved_account_name", None)
            transfer_details["recipient"] = recipient
            state["transfer_details"] = transfer_details

            # Add to missing slots
            if not state.get("missing_slots"):
                state["missing_slots"] = []
            if "recipient.account_number" not in state["missing_slots"]:
                state["missing_slots"].append("recipient.account_number")
            if "recipient.bank_code" not in state["missing_slots"]:
                state["missing_slots"].append("recipient.bank_code")

        return state
