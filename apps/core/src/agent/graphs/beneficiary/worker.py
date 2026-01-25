"""Beneficiary Worker.

Handles basic CRUD operations for beneficiaries using UnitOfWork.
"""

from typing import Any

from apps.core.src.agent.graphs.beneficiary.models import BeneficiaryIntent
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BeneficiaryWorker:
    """Worker for beneficiary operations."""

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> TransactionResult:
        """Run beneficiary operation."""
        try:
            intent = payload.get("intent")
            user_id = context.get("user_id") or payload.get("user_id")

            if not user_id:
                # Try to resolve user from phone if user_id missing
                phone = context.get("phone_number")
                if phone:
                    with UnitOfWork() as uow:
                        user = uow.users.get_by_phone(phone)
                        if user:
                            user_id = str(user.id)

            if not user_id:
                return TransactionResult(outcome=TransactionOutcome.FAILED, error="User identification failed.")

            if intent == BeneficiaryIntent.LIST or payload.get("list_intent"):
                return self._list_beneficiaries(user_id)

            elif intent == BeneficiaryIntent.ADD:
                return await self._add_beneficiary(user_id, payload, context)

            elif intent == BeneficiaryIntent.DELETE:
                return self._delete_beneficiary(user_id, payload)

            elif intent == BeneficiaryIntent.UPDATE:
                return self._update_beneficiary(user_id, payload)

            # Default to list if ambiguous but routed here
            return self._list_beneficiaries(user_id)

        except Exception as e:
            logger.error("beneficiary_worker_error", error=str(e), exc_info=True)
            return TransactionResult(outcome=TransactionOutcome.FAILED, error="Failed to process beneficiary request.")

    def _list_beneficiaries(self, user_id: str) -> TransactionResult:
        with UnitOfWork() as uow:
            beneficiaries = uow.beneficiaries.get_all_by_user(user_id)

            if not beneficiaries:
                return TransactionResult(
                    outcome=TransactionOutcome.OK, response="You haven't saved any beneficiaries yet."
                )

            lines = ["*Saved Beneficiaries*", ""]
            for b in beneficiaries:
                alias = b.alias or b.account_name
                detail_parts = []
                if b.bank_name:
                    detail_parts.append(b.bank_name)
                if b.account_number:
                    masked = f"…{b.account_number[-4:]}"
                    detail_parts.append(masked)

                details = " • ".join(detail_parts)
                lines.append(f"• **{alias}** ({details})")

            return TransactionResult(outcome=TransactionOutcome.OK, response="\n".join(lines))

    async def _add_beneficiary(self, user_id: str, payload: dict, context: dict) -> TransactionResult:
        # Check required fields
        # Ideally, we should have an extraction step before this to ensure we have data.
        # But for now, we assume planner/extractor did its job or we fail gracefully.

        name = payload.get("name") or payload.get("account_name")
        alias = payload.get("alias")
        account_number = payload.get("account_number")
        bank_code = payload.get("bank_code")
        bank_name = payload.get("bank_name")

        if not account_number or (not bank_code and not bank_name):
            return TransactionResult(
                outcome=TransactionOutcome.FAILED, error="I need the account number and bank to add a beneficiary."
            )

        provider = context.get("banking_provider")
        resolved_name = None

        if provider:
            # 1. Resolve Bank Code if missing
            if not bank_code and bank_name:
                try:
                    banks_resp = await provider.get_banks()
                    if banks_resp.get("success"):
                        # Fuzzy match or exact match
                        target = bank_name.lower()
                        for b in banks_resp.get("banks", []):
                            if b["name"].lower() == target or target in b["name"].lower():
                                bank_code = b["code"]
                                # Use official bank name
                                bank_name = b["name"]
                                break
                except Exception:
                    logger.warning("bank_resolution_failed")

            # 2. Resolve Account Name
            if account_number and bank_code:
                try:
                    resolved = await provider.resolve_account_number(account_number, bank_code)
                    if resolved:
                        resolved_name = resolved.account_name
                        # Auto-correct bank code/name if provider returns it
                        if resolved.bank_code:
                            bank_code = resolved.bank_code
                except Exception as e:
                    logger.warning("account_resolution_failed", error=str(e))
                    return TransactionResult(
                        outcome=TransactionOutcome.FAILED,
                        error=f"Could not verify account {account_number}. Please check the details.",
                    )

            if not resolved_name and provider:
                # If provider exists but resolution returned None or failed silently
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=f"Account lookup failed/invalid for {account_number} at {bank_name}.",
                )

        # Use resolved name if available, else provided name (if provider wasn't available)
        final_account_name = resolved_name or name or ""

        with UnitOfWork() as uow:
            uow.beneficiaries.create(
                user_id=user_id,
                account_number=account_number,
                bank_code=bank_code or "999",  # Fallback
                bank_name=bank_name or "Unknown Bank",
                account_name=final_account_name,
                alias=alias or name or final_account_name or "My Beneficiary",
                beneficiary_type="transfer",
            )
            uow.commit()

        display_name = alias or final_account_name or "Beneficiary"
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=f"✓ Verified & Added **{display_name}** ({final_account_name}) to your beneficiaries.",
        )

    def _delete_beneficiary(self, user_id: str, payload: dict) -> TransactionResult:
        target = payload.get("target_alias") or payload.get("name") or payload.get("alias")
        if not target:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED, error="Please specify which beneficiary to remove."
            )

        with UnitOfWork() as uow:
            # Fuzzy search or match by alias logic needed in Repo?
            # Generally UOW repo might only have list. We filter here or assume exact match for now.
            # Ideally repo has `find_by_alias`.
            # We'll fetch all and fuzzy match in code for simplicity or simplicity.

            all_bens = uow.beneficiaries.get_all_by_user(user_id)
            match = None

            # Try exact match on alias
            for b in all_bens:
                if (b.alias and b.alias.lower() == target.lower()) or (
                    b.account_name and b.account_name.lower() == target.lower()
                ):
                    match = b
                    break

            if not match:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED, error=f"I couldn't find a beneficiary named '{target}'."
                )

            uow.beneficiaries.delete(match.id)
            uow.commit()

        return TransactionResult(outcome=TransactionOutcome.OK, response=f"Deleted **{target}** from beneficiaries.")

    def _update_beneficiary(self, user_id: str, payload: dict) -> TransactionResult:
        # Placeholder for update logic
        return TransactionResult(
            outcome=TransactionOutcome.FAILED, error="Updating beneficiaries is not yet supported."
        )
