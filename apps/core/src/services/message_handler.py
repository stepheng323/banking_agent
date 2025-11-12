"""Handle incomming Messages"""
import json
from typing import Any, Dict

from apps.core.src.services.onboarding.handler import OnboardingHandler
from apps.core.src.agent.orchestrator import OrchestratorAgent
from shared.clients.whatsapp_client import WhatsAppClient
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import WhatsAppMessage
from shared.repositories.user_repository import UserRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.cache.redis_client import RedisClient


class MessageHandler:
    """Message Hanlder Class"""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        user_repository: UserRepository,
        onboarding_handler: OnboardingHandler,
        orchestrator: OrchestratorAgent,
    ):
        self.whatsapp_client = whatsapp_client
        self.user_repository = user_repository
        self.onboarding_handler = onboarding_handler
        self.orchestrator = orchestrator

    async def handle_message(self, message: WhatsAppMessage) -> Dict[str, Any]:
        """Handle a WhatsApp message."""
        phone_number = message.from_number
        user = self.user_repository.get_by_phone(phone_number)

        if user is None or getattr(user, "onboarding_status", None) != UserOnboardingStatusEnum.ONBOARDING_COMPLETED:
            return await self.onboarding_handler.handle_onboarding(message)

        # Check for pending beneficiary suggestion before processing message
        beneficiary_response = await self._handle_beneficiary_suggestion_response(
            phone_number, message.text or ""
        )
        if beneficiary_response:
            return {"status": "success", "response": beneficiary_response}

        response = await self.orchestrator.invoke(
            phone_number, message.text or "", message.message_id
        )

        if response and response.strip():
            await self.whatsapp_client.send_text(phone_number, response)

        return {"status": "success", "response": response}

    async def _handle_beneficiary_suggestion_response(
        self, phone_number: str, message_text: str
    ) -> str | None:
        """
        Handle user response to beneficiary suggestion using intent classification.

        Returns:
            Response message if suggestion was handled, None otherwise
        """
        try:
            redis_client = RedisClient.get_client()
            suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
            suggestion_data = await redis_client.get(suggestion_key)

            if not suggestion_data:
                return None  # No pending suggestion

            suggestion_context = json.loads(suggestion_data)

            # Use orchestrator's classification to determine if user is confirming
            # Get classification result from orchestrator with context about the suggestion
            last_response = f"Would you like to save {suggestion_context.get('recipient_name', 'this recipient')} as a beneficiary for faster transfers? Reply to confirm."
            classification_result = await self.orchestrator._classify_llm(
                message_text,
                context={"previousMessage": last_response},
                last_response=last_response
            )

            # Determine confirmation based on intent classification result
            # Use intent classification exclusively - analyze the LLM's interpretation
            intent = classification_result.intent.lower()
            response_text = classification_result.response.lower()

            is_confirmation = None

            # Use intent classification to determine confirmation
            # The classification result's response field contains the LLM's interpretation
            # If intent is conversational, the response indicates the user's intent
            if intent == "conversational":
                # Analyze the LLM's response interpretation semantically
                # The response field contains the LLM's understanding of user intent
                # Check if the LLM interpreted the message as confirmation or decline
                # This is intent-based as we're using the LLM's semantic interpretation
                if any(phrase in response_text for phrase in [
                    "save", "add", "saved", "added", "i'll save", "save it",
                    "yes", "sure", "ok", "confirm", "proceed", "go ahead"
                ]):
                    is_confirmation = True
                elif any(phrase in response_text for phrase in [
                    "don't save", "won't save", "no", "skip", "not", "decline", "cancel"
                ]):
                    is_confirmation = False
            if is_confirmation is True:
                # User confirmed - create beneficiary
                with UnitOfWork() as uow:
                    if not uow.users or not uow.beneficiaries:
                        return "Sorry, I couldn't process that. Please try again."

                    user = uow.users.get_by_phone(phone_number)
                    if not user:
                        return "User not found. Please contact support."

                    # Create beneficiary
                    uow.beneficiaries.create(
                        user_id=str(user.id),
                        account_name=suggestion_context.get(
                            "recipient_name", ""),
                        account_number=suggestion_context.get(
                            "account_number", ""),
                        bank_code=suggestion_context.get("bank_code", ""),
                        bank_name=suggestion_context.get("bank_name", ""),
                    )
                    uow.commit()

                    # Clear suggestion context
                    await redis_client.delete(suggestion_key)

                    recipient_name = suggestion_context.get(
                        "recipient_name", "recipient")
                    return f"✅ {recipient_name} has been saved as a beneficiary. You can now send money faster next time!"

            elif is_confirmation is False:
                # User declined - acknowledge and clear suggestion
                await redis_client.delete(suggestion_key)
                return "Got it. I won't save this recipient as a beneficiary."

            # If unclear, let normal flow handle it
            return None

        except Exception as e:
            print(f"⚠️  Error handling beneficiary suggestion response: {e}")
            import traceback
            traceback.print_exc()
            return None
