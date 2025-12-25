"""Quote service - initiates transactions from quoted messages."""

from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.sub_agents.interfaces import ITransactionService


class QuoteService:
    """Routes quoted message transactions to appropriate service."""
    
    SERVICE_MAP = {
        "transfer_success": "transfer",
        "transfer_confirmation": "transfer",
        "airtime_success": "airtime",
        "airtime_confirmation": "airtime",
    }
    
    def __init__(self, services: dict[str, ITransactionService]):
        self.services = services

    async def initiate_transaction(self, context: MessageContext):
        if not context.quoted_message_data:
            return None
        
        message_type = context.quoted_message_data.get("type")
        message_data = context.quoted_message_data.get("data", {})
        
        service_key = self.SERVICE_MAP.get(message_type)
        if not service_key:
            return None
        
        service = self.services.get(service_key)
        if not service:
            return None
        
        task_params = dict(message_data)
        
        if "recipient_name" in task_params:
            task_params["recipient"] = task_params.pop("recipient_name")
        if "recipient_account" in task_params:
            task_params["account_number"] = task_params.pop("recipient_account")
        if "recipient_bank_name" in task_params:
            task_params["bank_name"] = task_params.pop("recipient_bank_name")
        
        if context.intent == "modify_transaction" and context.classification_result:
            new_amount = context.classification_result.task_parameters.get("new_amount")
            if new_amount:
                task_params["amount"] = new_amount
        
        classification_result = {
            "intent": context.intent,
            "task_parameters": task_params
        }
        
        await service.run_simple(
            phone=context.phone_number,
            text=context.text,
            classification_result=classification_result
        )
