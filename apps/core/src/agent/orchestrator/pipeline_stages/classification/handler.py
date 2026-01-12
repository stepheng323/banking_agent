"""Classification handler - classifies user intent."""

import asyncio
from typing import Literal, TypedDict

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline_stages.classification.service import (
    OrchestratorClassificationService,
)
from apps.core.src.agent.orchestrator.pipeline_stages.context_loader.service import OrchestratorContextManager
from shared.repositories.actionable_message_repository import ActionableMessageRepository


class TransferMessageData(TypedDict, total=False):
    """Data stored for transfer success/confirmation messages."""

    amount: float
    recipient_name: str
    recipient_account: str
    recipient_bank_code: str
    recipient_bank_name: str
    source_account_id: str
    transaction_id: str


class AirtimeMessageData(TypedDict, total=False):
    """Data stored for airtime success messages."""

    amount: float
    phone_number: str
    network: str
    transaction_id: str


class QuotedMessageContext(TypedDict):
    """Typed dict for quoted message context passed to classification."""

    type: Literal[
        "transfer_success", "airtime_success", "transfer_confirmation", "airtime_confirmation"
    ]
    data: TransferMessageData | AirtimeMessageData


class ClassificationHandler(MessageHandler):
    """
    Classifies user intent using LLM.

    Runs after context loading to classify the message.
    """

    def __init__(
        self,
        classification_service: OrchestratorClassificationService,
        context_manager: OrchestratorContextManager,
        actionable_message_repo: ActionableMessageRepository,
    ):
        self.classification_service = classification_service
        self.context_manager = context_manager
        self.actionable_message_repo = actionable_message_repo

    async def can_handle(self, context: MessageContext) -> bool:
        """Always runs to classify intent."""
        return context.classification_result is None

    async def handle(self, context: MessageContext) -> MessageContext:
        """Classify user intent with quote context if available."""
        classification_context = {}
        if context.conversation_state:
            classification_context["conversationState"] = context.conversation_state

        if context.suggestion_context:
            classification_context["pendingBeneficiarySuggestion"] = context.suggestion_context

        if context.user_context:
            classification_context["userContext"] = context.user_context

        quoted_message_data = None
        if context.quoted_message_id:
            quoted_context = self._get_quoted_message_context(context.quoted_message_id)
            if quoted_context:
                classification_context["quotedMessage"] = quoted_context
                quoted_message_data = quoted_context
            else:
                classification_context["quotedMessageNotFound"] = True

        result = await self.classification_service.classify(
            context.text, classification_context, context.last_response, context.image_data
        )

        asyncio.create_task(
            self.context_manager.save_classification_result(context.phone_number, result)
        )

        if result.detected_language:
            asyncio.create_task(
                self.context_manager.set_user_language(
                    context.phone_number, result.detected_language
                )
            )

        return context.update(classification_result=result, quoted_message_data=quoted_message_data)

    def _get_quoted_message_context(self, wa_message_id: str) -> QuotedMessageContext | None:
        """Look up quoted message in database and return typed context."""
        try:
            message = self.actionable_message_repo.get_by_wa_message_id(wa_message_id)
            if message:
                return QuotedMessageContext(
                    type=message.message_type,
                    data=message.message_data,
                )
            return None
        except Exception:
            return None
