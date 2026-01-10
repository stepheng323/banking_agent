"""Quote service - initiates transactions from quoted messages."""

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.registry import ExecutorRegistry


class QuoteService:
    """Routes quoted message transactions to appropriate service."""

    MINIMAL_SERVICES_MAP = {
        "transfer_success": "transfer",
        "transfer_confirmation": "transfer",
        "airtime_success": "airtime",
        "airtime_confirmation": "airtime",
    }

    def __init__(self, registry: ExecutorRegistry):
        self.registry = registry

    async def initiate_transaction(self, context: MessageContext):
        if not context.quoted_message_data:
            return None

        message_type = context.quoted_message_data.get("type")

        service_key = self.MINIMAL_SERVICES_MAP.get(message_type)
        if not service_key:
            return None

        service = self.registry.get(service_key)
        if not service:
            return None

        await service.run_simple(
            phone=context.phone_number,
            text=context.text,
            classification_result={"intent": context.intent},
            quoted_data=context.quoted_message_data,
        )
