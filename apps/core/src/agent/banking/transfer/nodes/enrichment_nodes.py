# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false, reportUnknownVariableType=false
"""Context enrichment nodes for the transfer agent."""
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.tools.account_tools import get_user_accounts, get_account_balance
from apps.core.src.agent.banking.tools.transfer_tools import search_beneficiaries

from shared.repositories import UnitOfWork, UserRepository, BeneficiaryRepository




class EnrichmentNodes:
    """Nodes for loading user context and matching beneficiaries."""

    def __init__(self, llm: Any, beneficiary_repo: Optional[Any] = None) -> None:  # noqa: ARG002
        """Initialize with LLM instance.

        Args:
            llm: LLM instance for context enrichment
            beneficiary_repo: Deprecated, kept for backward compatibility
        """
        self.llm = llm

    async def enrich_accounts_node(self, state: TransferState) -> dict:
        """Fetch user accounts (runs in parallel with other enrichment nodes)."""
        phone_number = state["phone_number"]
        
        # Only fetch if not already loaded
        if not state.get("user_accounts"):
            accounts_result = get_user_accounts.invoke({"phone_number": phone_number})
            if accounts_result.get("success"):
                accounts = accounts_result.get("accounts", [])
                print(f"📊 Loaded {len(accounts)} accounts")
                # Return only the field this node modifies (reducer will merge)
                return {"user_accounts": accounts}
        
        # Return empty list to initialize the field (reducer will merge)
        return {"user_accounts": []}

    async def enrich_beneficiaries_node(self, state: TransferState) -> dict:
        """Fetch user beneficiaries (runs in parallel with other enrichment nodes)."""
        phone_number = state["phone_number"]
        
        # Only fetch if not already loaded
        if not state.get("user_beneficiaries"):
            beneficiary_result = self._get_all_beneficiaries(phone_number)
            if beneficiary_result.get("success"):
                matches = beneficiary_result.get("matches", [])
                print(f"👥 Loaded {len(matches)} beneficiaries")
                # Return only the fields this node modifies (reducer will merge)
                return {
                    "user_beneficiaries": matches,
                    "all_beneficiaries": matches
                }
        
        # Return empty lists to initialize the fields (reducer will merge)
        return {"user_beneficiaries": [], "all_beneficiaries": []}

    async def enrich_balance_node(self, state: TransferState) -> dict:
        """Fetch balance if calculation needed (runs in parallel with other enrichment nodes)."""
        phone_number = state["phone_number"]
        transfer_details = state.get("transfer_details", {})
        amount_details = transfer_details.get("amount") or {}
        
        # Only fetch balance if amount needs calculation
        if amount_details.get("needs_calculation"):
            # Determine which account to fetch balance for
            accounts = state.get("user_accounts", [])
            account_id = None
            
            if len(accounts) == 1:
                account_id = accounts[0]["id"]
            elif transfer_details.get("source_account", {}).get("account_id"):
                account_id = transfer_details["source_account"]["account_id"]
            
            if account_id:
                balance_result = get_account_balance.invoke({
                    "phone_number": phone_number,
                    "account_id": account_id,
                })
                
                if balance_result.get("success"):
                    print(f"💰 Loaded balance: ₦{balance_result.get('balance', 0):,.2f}")
                    # Return only the modified transfer_details
                    updated_details = {**transfer_details}
                    updated_amount = {**amount_details, "source_data": balance_result}
                    updated_details["amount"] = updated_amount
                    return {"transfer_details": updated_details}
        
        # Return empty dict - no modifications needed
        return {}

    async def match_beneficiaries_node(self, state: TransferState) -> TransferState:
        """Match recipient names with saved beneficiaries (runs after parallel fetch)."""
        phone_number = state["phone_number"]
        transfer_details = state.get("transfer_details", {})
        recipients = transfer_details.get("recipients") or []
        current_index = transfer_details.get("current_recipient_index", 0)

        # Determine active recipient
        active_recipient = (
            recipients[current_index]
            if recipients and 0 <= current_index < len(recipients)
            else transfer_details.get("recipient", {}) or {}
        )

        # Attempt to match recipient name with saved beneficiaries
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
                    # Auto-match with high confidence
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
                    # Multiple matches - need clarification
                    if not state.get("clarifications_needed"):
                        state["clarifications_needed"] = []
                    state["clarifications_needed"].append({
                        "type": "ambiguous_recipient",
                        "options": matches[:5],
                    })
                    print(f"⚠️ Found {len(matches)} potential matches")

                elif len(matches) == 0:
                    # No match - treat as new beneficiary
                    active_recipient["is_new_beneficiary"] = True
                    print("""🆕 No match found - treating as new beneficiary""")

        # Update state with matched recipient
        if recipients and 0 <= current_index < len(recipients):
            recipients[current_index] = active_recipient
            transfer_details["recipients"] = recipients
            transfer_details["recipient"] = active_recipient
        else:
            transfer_details["recipient"] = active_recipient

        state["transfer_details"] = transfer_details
        state["conversation_stage"] = "gathering"
        return state

    def _get_all_beneficiaries(self, phone_number: str, db_session: Optional[Session] = None) -> Dict[str, Any]:
        """Get all saved beneficiaries for a user.

        Args:
            phone_number: User's phone number
            db_session: Optional existing session to reuse. If None, creates a UnitOfWork.
        """
        owns_session = db_session is None

        try:
            if owns_session:
               # Create new UnitOfWork for this operation
                with UnitOfWork() as uow:
                    user = uow.users.get_by_phone(phone_number)

                    if not user:
                        return {"success": True, "matches": [], "count": 0}

                    beneficiaries = uow.beneficiaries.get_all_for_user(
                        str(user.id))
                    beneficiaries_list = []
                    for ben in beneficiaries:
                        beneficiaries_list.append({
                            "id": str(ben.id),
                            "name": ben.account_name,
                            "nickname": ben.alias or ben.account_name,
                            "account_number": ben.account_number,
                            "bank_name": ben.bank_name,
                            "bank_code": ben.bank_code,
                        })

                    return {"success": True, "matches": beneficiaries_list, "count": len(beneficiaries_list)}
            else:

                user_repo = UserRepository(db_session)
                beneficiary_repo = BeneficiaryRepository(db_session)

                user = user_repo.get_by_phone(phone_number)

                if not user:
                    return {"success": True, "matches": [], "count": 0}

                beneficiaries = beneficiary_repo.get_all_for_user(str(user.id))
                beneficiaries_list = []
                for ben in beneficiaries:
                    beneficiaries_list.append({
                        "id": str(ben.id),
                        "name": ben.account_name,
                        "nickname": ben.alias or ben.account_name,
                        "account_number": ben.account_number,
                        "bank_name": ben.bank_name,
                        "bank_code": ben.bank_code,
                    })

                return {"success": True, "matches": beneficiaries_list, "count": len(beneficiaries_list)}
        except Exception as e:
            return {"success": False, "error": f"Failed to load beneficiaries: {str(e)}", "matches": [], "count": 0}

    async def context_enricher_node(self, state: TransferState) -> TransferState:
        """Load user context and match beneficiaries."""
        print("🔍 CONTEXT ENRICHER: Loading user data...")

        phone_number = state["phone_number"]
        transfer_details = state.get("transfer_details", {})
        recipients = transfer_details.get("recipients") or []
        current_index = transfer_details.get("current_recipient_index", 0)

        all_beneficiaries_result = self._get_all_beneficiaries(phone_number)
        if all_beneficiaries_result.get("success"):
            state["all_beneficiaries"] = all_beneficiaries_result.get(
                "matches", [])
            print(
                f"   📋 Loaded {len(state['all_beneficiaries'])} saved beneficiaries for context")

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
            db_session = state.get("_db_session")
            beneficiary_result = self._get_all_beneficiaries(
                phone_number, db_session)
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
                    print("""🆕 No match found - treating as new beneficiary""")

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
