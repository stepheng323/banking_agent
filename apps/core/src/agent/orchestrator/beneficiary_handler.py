"""Beneficiary suggestion response handler for the orchestrator."""

from typing import Optional
import asyncio
import traceback

from shared.repositories.unit_of_work import UnitOfWork
from shared.cache.redis_client import RedisClient
from apps.core.src.agent.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.context_manager import OrchestratorContextManager


class OrchestratorBeneficiaryHandler:
    """Handles beneficiary suggestion responses."""

    def __init__(
        self,
        context_manager: OrchestratorContextManager,
    ) -> None:
        self.context_manager = context_manager

    async def handle_beneficiary_response(
        self,
        phone_number: str,
        text: str,
        result: ClassificationResult,
        suggestion_context: dict,
    ) -> Optional[str]:
        """
        Handle user response to beneficiary suggestion.

        Args:
            phone_number: User's phone number
            text: User's message
            result: Classification result
            suggestion_context: Beneficiary suggestion context from Redis

        Returns:
            Response string if handled, None otherwise
        """
        intent = result.intent.lower()
        redis_client = RedisClient.get_client()
        suggestion_key = f"user:{phone_number}:beneficiary_suggestion"

        print(
            f"DEBUG beneficiary suggestion: intent={intent}, extracted_alias={result.extracted_alias}, text='{text}'")

        if intent in ("yes", "confirm", "proceed"):
            try:
                with UnitOfWork() as uow:
                    if not uow.users or not uow.beneficiaries:
                        response = "Sorry, I couldn't process that. Please try again."
                        asyncio.create_task(
                            self.context_manager.save_last_response(phone_number, response))
                        return response

                    user = uow.users.get_by_phone(phone_number)
                    if not user:
                        response = "User not found. Please contact support."
                        asyncio.create_task(
                            self.context_manager.save_last_response(phone_number, response))
                        return response

                    alias = result.extracted_alias if result.extracted_alias else None
                    uow.beneficiaries.create(
                        user_id=str(user.id),
                        beneficiary_type="transfer",  # Default to transfer for existing flow
                        account_name=suggestion_context.get(
                            "recipient_name", ""),
                        account_number=suggestion_context.get(
                            "account_number", ""),
                        bank_code=suggestion_context.get("bank_code", ""),
                        bank_name=suggestion_context.get("bank_name", ""),
                        alias=alias,
                    )
                    uow.commit()

                    await redis_client.delete(suggestion_key)

                    try:
                        await redis_client.delete(f"user:{phone_number}:conversation_state")
                        await redis_client.delete(f"user:{phone_number}:transfer_session_start")
                    except Exception:
                        pass

                    # Show confirmation message
                    recipient_name = suggestion_context.get(
                        "recipient_name", "recipient")
                    if alias:
                        response = f"✅ Saved as '{alias}'. You can now use this alias next time."
                    else:
                        response = f"✅ Saved {recipient_name} as a beneficiary."
                    asyncio.create_task(
                        self.context_manager.save_last_response(phone_number, response))
                    return response
            except Exception as e:
                print(f"⚠️  Error creating beneficiary: {e}")
                traceback.print_exc()
        elif intent in ("no", "skip", "cancel") and text.strip().lower() in ("no", "n", "skip", "cancel", "don't", "dont", "not now", "notnow"):
            # Only decline if text is explicitly a decline word
            try:
                await redis_client.delete(suggestion_key)
                response = "Got it. I won't save this recipient as a beneficiary."
                asyncio.create_task(
                    self.context_manager.save_last_response(phone_number, response))
                return response
            except Exception as e:
                print(f"⚠️  Error clearing beneficiary suggestion: {e}")
        else:
            alias_text = result.extracted_alias if result.extracted_alias else text.strip()

            if alias_text:
                try:
                    with UnitOfWork() as uow:
                        if not uow.users or not uow.beneficiaries:
                            response = "Sorry, I couldn't process that. Please try again."
                            asyncio.create_task(
                                self.context_manager.save_last_response(phone_number, response))
                            return response
                        user = uow.users.get_by_phone(phone_number)
                        if not user:
                            response = "User not found. Please contact support."
                            asyncio.create_task(
                                self.context_manager.save_last_response(phone_number, response))
                            return response
                        uow.beneficiaries.create(
                            user_id=str(user.id),
                            beneficiary_type="transfer",  # Default to transfer for existing flow
                            account_name=suggestion_context.get(
                                "recipient_name", ""),
                            account_number=suggestion_context.get(
                                "account_number", ""),
                            bank_code=suggestion_context.get(
                                "bank_code", ""),
                            bank_name=suggestion_context.get(
                                "bank_name", ""),
                            alias=alias_text[:64],
                        )
                        uow.commit()
                        await redis_client.delete(suggestion_key)
                        try:
                            await redis_client.delete(f"user:{phone_number}:conversation_state")
                            await redis_client.delete(f"user:{phone_number}:transfer_session_start")
                        except Exception:
                            pass
                        response = f"✅ Saved as '{alias_text}'. You can now use this alias next time."
                        asyncio.create_task(
                            self.context_manager.save_last_response(phone_number, response))
                        return response
                except Exception as e:
                    print(
                        f"⚠️  Error creating beneficiary (alias path): {e}")
                    traceback.print_exc()

        return None

