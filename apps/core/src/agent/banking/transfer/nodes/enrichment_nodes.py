# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false, reportUnknownVariableType=false
"""Context enrichment nodes for the transfer agent."""
from typing import Any, Dict

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.utils.beneficiary_matcher import match_beneficiaries
from apps.core.src.agent.banking.tools.account_tools import get_user_accounts, get_account_balance


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

            # Case-insensitive partial match
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

        # Load user accounts
        if not state.get("user_accounts"):
            accounts_result = get_user_accounts.invoke(
                {"phone_number": phone_number})
            if accounts_result.get("success"):
                state["user_accounts"] = accounts_result.get("accounts", [])
                print(f"📊 Loaded {len(state['user_accounts'])} accounts")

        # Load beneficiaries
        if not state.get("user_beneficiaries"):
            beneficiary_result = self._find_recipient_by_name(phone_number, "")
            if beneficiary_result.get("success"):
                state["user_beneficiaries"] = beneficiary_result.get(
                    "matches", [])
                print(
                    f"👥 Loaded {len(state['user_beneficiaries'])} beneficiaries")

        # Match recipient if name provided
        recipient_name = state["transfer_details"]["recipient"].get("name")
        if recipient_name and not state["transfer_details"]["recipient"].get("matched_beneficiary_id"):
            beneficiaries = state.get("user_beneficiaries", []) or []
            matches = match_beneficiaries(recipient_name, beneficiaries)

            if len(matches) == 1 and matches[0]["confidence_score"] >= 90:
                # High confidence single match
                matched = matches[0]
                state["transfer_details"]["recipient"].update({
                    "matched_beneficiary_id": matched["id"],
                    "account_number": matched["account_number"],
                    "bank_code": matched["bank_code"],
                    "bank_name": matched["bank_name"],
                    "confidence_score": matched["confidence_score"],
                    "is_new_beneficiary": False,
                })
                print(
                    f"✅ Auto-matched beneficiary: {matched['name']} ({matched['confidence_score']}%)")

            elif len(matches) > 1:
                # Multiple matches
                state["clarifications_needed"] = state.get(
                    "clarifications_needed", [])
                state["clarifications_needed"].append({
                    "type": "ambiguous_recipient",
                    "options": matches[:5],
                })
                print(f"⚠️ Found {len(matches)} potential matches")

            elif len(matches) == 0:
                state["transfer_details"]["recipient"]["is_new_beneficiary"] = True
                print(f"🆕 No match found - treating as new beneficiary")

        # Load balance if calculation needed
        if state["transfer_details"]["amount"].get("needs_calculation"):
            accounts = state.get("user_accounts", []) or []
            if len(accounts) == 1:
                account_id = accounts[0]["id"]
            elif state["transfer_details"]["source_account"].get("account_id"):
                account_id = state["transfer_details"]["source_account"]["account_id"]
            else:
                account_id = None

            if account_id:
                balance_result = get_account_balance.invoke(
                    {"account_id": account_id})
                if balance_result.get("success"):
                    state["transfer_details"]["amount"]["source_data"] = balance_result
                    print(
                        f"💰 Loaded balance: ₦{balance_result.get('balance', 0):,.2f}")

        state["conversation_stage"] = "gathering"
        return state
