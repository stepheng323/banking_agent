# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false, reportUnknownVariableType=false
"""Context enrichment nodes for the transfer agent."""
from typing import Any, Dict

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.tools.account_tools import get_user_accounts, get_account_balance
from apps.core.src.agent.banking.tools.transfer_tools import search_beneficiaries


class EnrichmentNodes:
    """Nodes for loading user context and matching beneficiaries."""

    def __init__(self, llm: Any) -> None:
        """Initialize with LLM instance."""
        self.llm = llm

    def _find_recipient_by_name(self, phone_number: str, name: str) -> Dict[str, Any]:
        """Find saved recipients/beneficiaries by name."""
        # TODO: Query from database
        try:
            # Placeholder - saved beneficiaries
            beneficiaries = [
                {
                    "id": "ben_001",
                    "name": "Mum",
                    "account_number": "1234567890",
                    "bank_name": "GTBank",
                    "bank_code": "058"
                },
                {
                    "id": "ben_002",
                    "name": "Dad",
                    "account_number": "0987654321",
                    "bank_name": "FirstBank",
                    "bank_code": "011"
                },
                {
                    "id": "ben_003",
                    "name": "Sister",
                    "account_number": "1111222233",
                    "bank_name": "Access Bank",
                    "bank_code": "044"
                },
            ]

            name_lower = name.lower()
            matches = [
                b for b in beneficiaries if name_lower in b["name"].lower()]

            if not matches:
                return {
                    "success": False,
                    "error": f"No recipient found matching '{name}'",
                    "suggestion": "Try using their full name or add them as a beneficiary first"
                }

            return {"success": True, "matches": matches, "count": len(matches)}

        except Exception as e:
            return {"success": False, "error": f"Search failed: {str(e)}"}

    async def context_enricher_node(self, state: TransferState) -> TransferState:
        """Load user context and match beneficiaries."""
        print("🔍 CONTEXT ENRICHER: Loading user data...")

        phone_number = state["phone_number"]
        transfer_details = state.get("transfer_details", {})
        recipients = transfer_details.get("recipients") or []
        current_index = transfer_details.get("current_recipient_index", 0)

        active_recipient = (
            recipients[current_index]
            if recipients and 0 <= current_index < len(recipients)
            else transfer_details.get("recipient", {}) or {}
        )

        if not state.get("user_accounts"):
            accounts_result = get_user_accounts.invoke(
                {"phone_number": phone_number})
            if accounts_result.get("success"):
                state["user_accounts"] = accounts_result.get("accounts", [])
                print(f"📊 Loaded {len(state['user_accounts'])} accounts")

        if not state.get("user_beneficiaries"):
            beneficiary_result = self._find_recipient_by_name(phone_number, "")
            if beneficiary_result.get("success"):
                state["user_beneficiaries"] = beneficiary_result.get(
                    "matches", [])
                print(
                    f"👥 Loaded {len(state['user_beneficiaries'])} beneficiaries")

        recipient_name = active_recipient.get("name")
        if recipient_name and not active_recipient.get("matched_beneficiary_id"):
            search_result = search_beneficiaries.invoke({
                "phone_number": phone_number,
                "search_term": recipient_name
            })

            if search_result.get("success"):
                matches = search_result.get("matches", [])
                best_match = matches[0] if matches else None
                confidence_threshold = 85 if len(matches) == 1 else 90

                if best_match and best_match["confidence_score"] >= confidence_threshold:
                    matched = best_match
                    active_recipient.update({
                        "matched_beneficiary_id": matched["id"],
                        "account_number": matched["account_number"],
                        "bank_code": matched["bank_code"],
                        "bank_name": matched["bank_name"],
                        "confidence_score": matched["confidence_score"],
                        "is_new_beneficiary": False,
                    })
                    print(
                        f"✅ Auto-matched beneficiary: {matched['name']} ({matched['confidence_score']}% confidence)")

                elif len(matches) > 1:
                    if not state.get("clarifications_needed"):
                        state["clarifications_needed"] = []
                    state["clarifications_needed"].append({
                        "type": "ambiguous_recipient",
                        "options": matches[:5],
                    })
                    print(f"⚠️ Found {len(matches)} potential matches")

                elif len(matches) == 0:
                    active_recipient["is_new_beneficiary"] = True
                    print(f"🆕 No match found - treating as new beneficiary")

        if recipients and 0 <= current_index < len(recipients):
            recipients[current_index] = active_recipient
            transfer_details["recipients"] = recipients
            transfer_details["recipient"] = active_recipient
        else:
            transfer_details["recipient"] = active_recipient

        amount_details = transfer_details.get("amount") or {}
        if amount_details.get("needs_calculation"):
            accounts = state.get("user_accounts", []) or []
            if len(accounts) == 1:
                account_id = accounts[0]["id"]
            elif transfer_details.get("source_account", {}).get("account_id"):
                account_id = transfer_details["source_account"]["account_id"]
            else:
                account_id = None

            if account_id:
                balance_result = get_account_balance.invoke(
                    {
                        "phone_number": phone_number,
                        "account_id": account_id,
                    }
                )
                if balance_result.get("success"):
                    amount_details["source_data"] = balance_result
                    print(
                        f"💰 Loaded balance: ₦{balance_result.get('balance', 0):,.2f}")

        transfer_details["amount"] = amount_details
        state["transfer_details"] = transfer_details
        state["conversation_stage"] = "gathering"
        return state
