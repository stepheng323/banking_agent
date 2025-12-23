"""Flow resume handler - detects and handles resume intent."""

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from .service import FlowContextService
from shared.services.affirmation import AffirmationService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FlowResumeHandler(MessageHandler):
    """
    Handles flow resume requests after mid-flow interrupts.
    
    When user responds with approval (any language) after we asked
    "Ready to continue your transfer?", this handler triggers resume.
    """
    
    def __init__(self, flow_context_service: FlowContextService):
        self.flow_context = flow_context_service
    
    async def can_handle(self, context: MessageContext) -> bool:
        """
        Can handle if:
        1. There's a paused flow
        2. User's message is an approval (any language)
        """
        paused = await self.flow_context.get_paused_flow(context.phone_number)
        if not paused:
            return False
        
        result = AffirmationService.classify_sync(context.text)
        if result.is_approval:
            return True
        
        if context.classification_result:
            intent = context.classification_result.intent
            if intent in ("yes", "confirm", "resume_flow"):
                return True
        
        return False
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """
        Resume the paused flow.
        
        Clear the pause marker and update classification to route to the subgraph.
        """
        paused = await self.flow_context.get_paused_flow(context.phone_number)
        if not paused:
            return context
        
        flow_type = paused.get("flow_type", "")
        
        await self.flow_context.clear_paused_flow(context.phone_number)
        
        logger.info("flow_resuming", phone=context.phone_number, flow_type=flow_type)
        
        new_classification = ClassificationResult(
            intent=flow_type,
            is_cancellation=False,
            is_complex=False,
            confidence=0.95,
            response="",
            complexity_reason="Flow resume after interrupt",
        )
        
        return context.update(
            classification_result=new_classification,
            is_flow_resume=True,
        )

